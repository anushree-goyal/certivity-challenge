"""Checks for the shared model adapter.

The adapter is the only module that touches the network, so these use an
injected fake client: they verify the adapter's own behaviour - credential
handling, schema enforcement, caching, budgets, refusals and vision payload
assembly - without making a paid call.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx2
import pytest

from certivity.contracts import (
    ImagePayload,
    LLMCallError,
    LLMConfigurationError,
    ModelBudgetExceeded,
)
from certivity.llm import ProviderConfig, StructuredLLMAdapter
from certivity.llm.cache import CallCache

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


@dataclass
class FakeBlock:
    text: str
    type: str = "text"


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 20
    cache_read_input_tokens: int = 5
    cache_creation_input_tokens: int = 0


@dataclass
class FakeResponse:
    text: str
    stop_reason: str = "end_turn"
    model: str = "claude-opus-5"
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_details: Any = None

    @property
    def content(self) -> list[FakeBlock]:
        return [FakeBlock(text=self.text)]


class FakeMessages:
    """Records requests and replays a queued script of responses."""

    def __init__(self, owner: "FakeClient") -> None:
        self.owner = owner

    def create(self, **kwargs: Any) -> Any:
        self.owner.requests.append(kwargs)
        if not self.owner.responses:
            raise AssertionError("the fake client ran out of scripted responses")
        nxt = self.owner.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class FakeNamespace:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.messages = FakeMessages(self)
        self.beta = FakeNamespace(self.messages)


def bad_request(message: str) -> anthropic.BadRequestError:
    """A real SDK exception, so the adapter's handling is tested against the
    actual class it will see in production rather than a stand-in."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(
        message=message, response=httpx2.Response(400, request=request), body=None
    )


def make_adapter(responses: list[Any], **kwargs: Any) -> tuple[StructuredLLMAdapter, FakeClient]:
    client = FakeClient(responses)
    config = kwargs.pop("config", ProviderConfig())
    adapter = StructuredLLMAdapter(config, client=client, **kwargs)
    return adapter, client


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_missing_credentials_raise_rather_than_degrade(monkeypatch, tmp_path):
    """An unavailable model must stop the run, not produce invented output."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ant auth profile on disk

    adapter = StructuredLLMAdapter()
    assert adapter.credentials_available() is False
    with pytest.raises(LLMConfigurationError) as excinfo:
        adapter.generate_json("t", "instructions", {}, SCHEMA)
    assert "credential" in str(excinfo.value).lower()


def test_credentials_are_detected_from_either_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    adapter = StructuredLLMAdapter()
    assert adapter.credentials_available() is True
    assert adapter.describe()["credential_source"] == "ANTHROPIC_API_KEY"


def test_describe_exposes_no_secret_values(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-super-secret-value")
    described = StructuredLLMAdapter().describe()
    assert "sk-ant-super-secret-value" not in repr(described)
    assert described["credentials_configured"] is True


# --------------------------------------------------------------------------- #
# The call itself
# --------------------------------------------------------------------------- #


def test_successful_call_returns_parsed_data_and_measured_usage():
    adapter, client = make_adapter([FakeResponse(text='{"answer": "red"}')])
    response = adapter.generate_json("extract", "instructions", {"k": "v"}, SCHEMA)

    assert response.data == {"answer": "red"}
    assert response.cached is False
    assert response.usage.calls == 1
    # Cache reads and writes count towards input tokens, so the run record
    # reflects what was actually processed.
    assert response.usage.input_tokens == 105
    assert response.usage.output_tokens == 20
    assert response.usage.by_model == {"claude-opus-5": 1}
    assert response.latency_ms >= 0


def test_request_carries_json_schema_output_config_and_effort():
    adapter, client = make_adapter([FakeResponse(text='{"answer": "ok"}')])
    adapter.generate_json("extract", "instructions", {"k": "v"}, SCHEMA)

    request = client.requests[0]
    assert request["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert request["output_config"]["effort"] == "high"
    assert request["model"] == "claude-opus-5"
    # The stable instructions are marked cacheable; they repeat every section.
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["system"][0]["text"] == "instructions"


def test_per_task_model_override_is_applied():
    adapter, client = make_adapter(
        [FakeResponse(text='{"answer": "ok"}', model="claude-haiku-4-5")],
        model_overrides={"bulk_task": "claude-haiku-4-5"},
    )
    adapter.generate_json("bulk_task", "instructions", {}, SCHEMA)
    assert client.requests[0]["model"] == "claude-haiku-4-5"


def test_images_are_base64_encoded_into_the_request(tmp_path):
    image = tmp_path / "drawing.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes")
    adapter, client = make_adapter([FakeResponse(text='{"answer": "ok"}')])

    adapter.generate_json(
        "product_profile",
        "instructions",
        {},
        SCHEMA,
        [ImagePayload(media_type="image/png", asset_ref=str(image), caption="view")],
    )

    blocks = client.requests[0]["messages"][0]["content"]
    image_blocks = [b for b in blocks if b["type"] == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(image_blocks[0]["source"]["data"]) == image.read_bytes()
    assert any("view" in b.get("text", "") for b in blocks if b["type"] == "text")


def test_missing_image_file_is_reported_not_skipped(tmp_path):
    adapter, _ = make_adapter([FakeResponse(text='{"answer": "ok"}')])
    with pytest.raises(LLMCallError, match="image asset not found"):
        adapter.generate_json(
            "product_profile",
            "instructions",
            {},
            SCHEMA,
            [ImagePayload(media_type="image/png", asset_ref=str(tmp_path / "nope.png"))],
        )


# --------------------------------------------------------------------------- #
# Schema enforcement
# --------------------------------------------------------------------------- #


def test_schema_violation_is_reasked_with_the_specific_errors():
    adapter, client = make_adapter(
        [
            FakeResponse(text='{"wrong_key": 1}'),
            FakeResponse(text='{"answer": "corrected"}'),
        ]
    )
    response = adapter.generate_json("extract", "instructions", {}, SCHEMA)

    assert response.data == {"answer": "corrected"}
    assert len(client.requests) == 2
    # The correction quotes the actual violation rather than repeating blindly.
    correction = client.requests[1]["messages"][0]["content"][-1]["text"]
    assert "answer" in correction
    assert any("violated the schema" in w for w in response.warnings)


def test_persistent_schema_violation_raises_rather_than_returning_bad_data():
    adapter, _ = make_adapter(
        [FakeResponse(text='{"wrong": 1}'), FakeResponse(text='{"still_wrong": 2}')]
    )
    with pytest.raises(LLMCallError, match="violated the schema"):
        adapter.generate_json("extract", "instructions", {}, SCHEMA)


def test_invalid_json_is_treated_as_a_schema_failure():
    adapter, _ = make_adapter(
        [FakeResponse(text="not json at all"), FakeResponse(text='{"answer": "ok"}')]
    )
    response = adapter.generate_json("extract", "instructions", {}, SCHEMA)
    assert response.data == {"answer": "ok"}


# --------------------------------------------------------------------------- #
# Budgets, refusals and degradation
# --------------------------------------------------------------------------- #


def test_budget_is_enforced_across_calls():
    adapter, _ = make_adapter(
        [FakeResponse(text='{"answer": "a"}'), FakeResponse(text='{"answer": "b"}')],
        max_calls=1,
    )
    adapter.generate_json("extract", "instructions", {"n": 1}, SCHEMA)
    with pytest.raises(ModelBudgetExceeded):
        adapter.generate_json("extract", "instructions", {"n": 2}, SCHEMA)


def test_a_refusal_is_reported_and_produces_no_requirement():
    adapter, _ = make_adapter([FakeResponse(text="", stop_reason="refusal")])
    with pytest.raises(LLMCallError, match="declined"):
        adapter.generate_json("extract", "instructions", {}, SCHEMA)


def test_truncated_output_is_warned_about():
    adapter, _ = make_adapter(
        [FakeResponse(text='{"answer": "ok"}', stop_reason="max_tokens")]
    )
    response = adapter.generate_json("extract", "instructions", {}, SCHEMA)
    assert any("max_tokens" in w for w in response.warnings)


def test_refusal_fallbacks_are_used_by_default_and_degrade_once_if_rejected():
    adapter, client = make_adapter(
        [
            bad_request("unsupported beta: server-side-fallback"),
            FakeResponse(text='{"answer": "ok"}'),
            FakeResponse(text='{"answer": "second"}'),
        ]
    )
    first = adapter.generate_json("extract", "instructions", {"n": 1}, SCHEMA)
    assert first.data == {"answer": "ok"}
    assert any("refusal fallbacks" in w for w in first.warnings)

    # The deployment's rejection is remembered: the next call does not retry it.
    adapter.generate_json("extract", "instructions", {"n": 2}, SCHEMA)
    assert adapter.describe()["refusal_fallbacks"] is False


def test_other_bad_requests_are_not_silently_retried():
    adapter, client = make_adapter(
        [
            bad_request("max_tokens must be positive")
        ]
    )
    with pytest.raises(LLMCallError, match="rejected"):
        adapter.generate_json("extract", "instructions", {}, SCHEMA)
    assert len(client.requests) == 1


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #


def test_identical_call_is_served_from_cache(tmp_path):
    cache = CallCache(tmp_path / "calls")
    adapter, client = make_adapter([FakeResponse(text='{"answer": "red"}')], cache=cache)

    first = adapter.generate_json("extract", "instructions", {"k": "v"}, SCHEMA)
    second = adapter.generate_json("extract", "instructions", {"k": "v"}, SCHEMA)

    assert first.cached is False and second.cached is True
    assert second.data == first.data
    assert len(client.requests) == 1, "the cached call must not reach the provider"
    assert second.usage.cached_calls == 1


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param({"payload": {"k": "other"}}, id="payload"),
        pytest.param({"instructions": "different instructions"}, id="instructions"),
        pytest.param({"prompt_version": "v2"}, id="prompt_version"),
        pytest.param({"schema": {"type": "object", "required": [], "properties": {}}}, id="schema"),
    ],
)
def test_cache_key_changes_when_anything_that_affects_the_answer_changes(tmp_path, mutate):
    """Plan rule 9: a cached result must match the current input and configuration."""
    cache = CallCache(tmp_path / "calls")
    adapter, client = make_adapter(
        [FakeResponse(text='{"answer": "a"}'), FakeResponse(text='{"answer": "b"}')],
        cache=cache,
    )
    base = {
        "task": "extract",
        "instructions": "instructions",
        "payload": {"k": "v"},
        "schema": SCHEMA,
        "prompt_version": "v1",
    }
    adapter.generate_json(
        base["task"], base["instructions"], base["payload"], base["schema"],
        prompt_version=base["prompt_version"],
    )
    changed = {**base, **mutate}
    second = adapter.generate_json(
        changed["task"], changed["instructions"], changed["payload"], changed["schema"],
        prompt_version=changed["prompt_version"],
    )
    assert second.cached is False
    assert len(client.requests) == 2


def test_cache_key_changes_with_model_configuration(tmp_path):
    cache = CallCache(tmp_path / "calls")
    args = ("extract", "instructions", {"k": "v"}, SCHEMA)

    first_adapter, first_client = make_adapter([FakeResponse(text='{"answer": "a"}')], cache=cache)
    first_adapter.generate_json(*args)

    second_adapter, second_client = make_adapter(
        [FakeResponse(text='{"answer": "b"}')],
        cache=cache,
        config=ProviderConfig(effort="low"),
    )
    assert second_adapter.generate_json(*args).cached is False
    assert len(second_client.requests) == 1


def test_a_corrupt_cache_entry_is_a_miss_not_a_crash(tmp_path):
    cache = CallCache(tmp_path / "calls")
    adapter, client = make_adapter(
        [FakeResponse(text='{"answer": "a"}'), FakeResponse(text='{"answer": "a"}')],
        cache=cache,
    )
    adapter.generate_json("extract", "instructions", {"k": "v"}, SCHEMA)
    for path in (tmp_path / "calls").rglob("*.json"):
        path.write_text("{ truncated")

    assert adapter.generate_json("extract", "instructions", {"k": "v"}, SCHEMA).cached is False
