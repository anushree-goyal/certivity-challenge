"""Retries, rate limiting and the measurement that makes them visible.

A run slowed down by a factor of three and nobody could say why, because the
only things that could have explained it - 429s, SDK-internal retries, request
latency - were all invisible. These cover the machinery that answers that
question, and the pacing that keeps eight workers from causing the problem they
are then asked to diagnose.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx2
import pytest

from certivity.contracts import LLMCallError
from certivity.llm import ProviderConfig, StructuredLLMAdapter
from certivity.llm.limiter import LimiterConfig, RateLimiter, limiter_from_env
from certivity.llm.telemetry import ApiTelemetry

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


# --------------------------------------------------------------------------- #
# A client that can fail on demand
# --------------------------------------------------------------------------- #


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 20
    cache_read_input_tokens: int = 5
    cache_creation_input_tokens: int = 1


@dataclass
class _Response:
    text: str = '{"answer": "ok"}'
    stop_reason: str = "end_turn"
    model: str = "claude-opus-5"
    usage: _Usage = field(default_factory=_Usage)
    stop_details: Any = None

    @property
    def content(self) -> list[_Block]:
        return [_Block(text=self.text)]


def _rate_limit_error(retry_after: str | None = None) -> anthropic.RateLimitError:
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx2.Response(
        429,
        headers={**headers, "anthropic-ratelimit-requests-remaining": "0"},
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    return anthropic.RateLimitError("rate limited", response=response, body=None)


class _Messages:
    def __init__(self, owner: "_Client") -> None:
        self.owner = owner

    def create(self, **kwargs: Any) -> Any:
        self.owner.attempts += 1
        if self.owner.failures:
            raise self.owner.failures.pop(0)
        return _Response()


class _Client:
    """Raises the queued failures, then succeeds. No `stream`, so create() is used."""

    def __init__(self, failures: list[BaseException] | None = None) -> None:
        self.failures = list(failures or [])
        self.attempts = 0
        self.messages = _Messages(self)
        self.beta = type("Beta", (), {"messages": self.messages})()


def _adapter(client: _Client, **config: Any) -> StructuredLLMAdapter:
    return StructuredLLMAdapter(
        ProviderConfig(use_refusal_fallbacks=False, **config), client=client
    )


def _call(adapter: StructuredLLMAdapter) -> Any:
    return adapter.generate_json("probe", "instructions", {"k": "v"}, SCHEMA)


# --------------------------------------------------------------------------- #
# Retries
# --------------------------------------------------------------------------- #


def test_a_rate_limited_call_is_retried_and_then_succeeds():
    client = _Client([_rate_limit_error("1")])
    adapter = _adapter(client, max_retries=2)

    response = _call(adapter)

    assert response.data == {"answer": "ok"}
    assert client.attempts == 2
    snapshot = adapter.telemetry.snapshot()
    assert snapshot["http"]["retries_observed"] == 1, (
        "a retry the caller cannot count is a retry nobody can diagnose"
    )


def test_the_providers_retry_after_is_obeyed_rather_than_guessed():
    """Guessing a delay when the server has stated one turns a pause into a wait."""
    adapter = _adapter(_Client(), max_retries=3)
    requested = adapter._retry_delay(_rate_limit_error("7"), attempt=0)
    assert requested == 7.0

    # Absent the header, backoff grows and is jittered rather than fixed.
    delays = [adapter._retry_delay(_rate_limit_error(), attempt=a) for a in range(4)]
    assert all(d > 0 for d in delays)
    assert max(delays) <= 30.0, "backoff must stay bounded"
    assert len(set(delays)) > 1, "identical delays would resynchronise every worker"


def test_a_pathological_retry_after_cannot_park_a_worker():
    adapter = _adapter(_Client(), max_retries=1)
    assert adapter._retry_delay(_rate_limit_error("100000"), attempt=0) == 60.0
    assert adapter._retry_delay(_rate_limit_error("0"), attempt=0) == 1.0


def test_exhausted_retries_surface_as_a_call_error_not_a_hang():
    client = _Client([_rate_limit_error("1"), _rate_limit_error("1")])
    adapter = _adapter(client, max_retries=1)

    with pytest.raises(LLMCallError, match="rate limited"):
        _call(adapter)
    assert client.attempts == 2
    assert adapter.telemetry.snapshot()["calls"]["failed"] == 1


def test_a_bad_request_is_not_retried():
    """Repeating a malformed request produces the same answer more slowly."""
    error = anthropic.BadRequestError(
        "bad",
        response=httpx2.Response(
            400, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        ),
        body=None,
    )
    client = _Client([error])
    adapter = _adapter(client, max_retries=3)

    with pytest.raises(LLMCallError):
        _call(adapter)
    assert client.attempts == 1


def test_a_timeout_is_retried_because_a_dropped_socket_is_not_a_bad_request():
    timeout = anthropic.APITimeoutError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    client = _Client([timeout])
    adapter = _adapter(client, max_retries=2)

    assert _call(adapter).data == {"answer": "ok"}
    assert client.attempts == 2


def test_the_sdk_is_told_not_to_retry_when_this_module_does():
    """Both layers retrying turns one logical attempt into nine."""
    assert ProviderConfig().explicit_retries is True
    assert ProviderConfig(explicit_retries=True).max_retries == 2


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #


def test_tokens_latency_and_cost_are_measured_from_the_response():
    adapter = _adapter(_Client())
    _call(adapter)

    snapshot = adapter.telemetry.snapshot()
    assert snapshot["tokens"] == {
        "input": 100, "output": 20, "cache_creation": 1, "cache_read": 5,
        "input_per_minute": pytest.approx(snapshot["tokens"]["input_per_minute"]),
        "output_per_minute": pytest.approx(snapshot["tokens"]["output_per_minute"]),
    }
    assert snapshot["latency_s"]["n"] == 1
    assert snapshot["calls"]["completed"] == 1
    assert snapshot["estimated_cost_usd"] > 0


def test_a_cached_call_is_counted_as_a_hit_and_costs_nothing(tmp_path):
    from certivity.llm.cache import CallCache

    client = _Client()
    adapter = StructuredLLMAdapter(
        ProviderConfig(use_refusal_fallbacks=False),
        client=client,
        cache=CallCache(tmp_path),
    )
    _call(adapter)
    _call(adapter)

    assert client.attempts == 1, "the second call reached the provider"
    assert adapter.telemetry.snapshot()["calls"]["cache_hits"] == 1


def test_the_transport_hook_sees_every_round_trip_including_sdk_retries():
    """This is the only place an SDK-internal retry is observable."""
    telemetry = ApiTelemetry()
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    telemetry.on_response(
        httpx2.Response(429, headers={"retry-after-ms": "1500"}, request=request)
    )
    telemetry.on_response(httpx2.Response(529, request=request))
    telemetry.on_response(httpx2.Response(200, request=request))

    snapshot = telemetry.snapshot()
    assert snapshot["http"]["requests"] == 3
    assert snapshot["http"]["rate_limited_429"] == 1
    assert snapshot["http"]["overloaded_529"] == 1
    assert snapshot["http"]["retry_after_s_total"] == 1.5


def test_the_diagnosis_rules_out_rate_limiting_when_there_are_no_429s():
    """The claim the slowdown needed and never got: evidence, or no claim."""
    telemetry = ApiTelemetry()
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    for _ in range(5):
        telemetry.on_response(httpx2.Response(200, request=request))
    telemetry.call_started("extract_obligations", "claude-sonnet-5")
    telemetry.call_finished(
        model="claude-sonnet-5", latency_s=180.0, input_tokens=9000, output_tokens=4000
    )

    lines = " ".join(telemetry.diagnosis())
    assert "no HTTP 429" in lines
    assert "latency" in lines


def test_a_telemetry_failure_never_fails_a_run():
    """A measurement problem must not fail extraction, nor invent a round trip."""
    telemetry = ApiTelemetry()
    telemetry.on_response(object())  # not a response at all
    telemetry.on_response(None)
    assert telemetry.snapshot()["http"]["requests"] == 0


# --------------------------------------------------------------------------- #
# Pacing
# --------------------------------------------------------------------------- #


def test_a_default_limiter_does_not_throttle_anything():
    """Concurrency defaults must not change until a measurement justifies it."""
    limiter = RateLimiter()
    assert limiter.config.active is False
    assert limiter.acquire(100_000) == 0.0
    limiter.release()


def test_concurrency_is_capped_across_threads():
    limiter = RateLimiter(LimiterConfig(max_concurrency=2))
    peak = {"n": 0, "now": 0}
    lock = threading.Lock()

    def worker() -> None:
        with limiter.slot():
            with lock:
                peak["now"] += 1
                peak["n"] = max(peak["n"], peak["now"])
            time.sleep(0.02)
            with lock:
                peak["now"] -= 1

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak["n"] <= 2, "the shared gate let more than its limit through"


def test_the_request_bucket_paces_once_its_burst_is_spent():
    limiter = RateLimiter(LimiterConfig(requests_per_minute=60))
    for _ in range(60):  # drain the burst
        limiter.acquire()
    started = time.monotonic()
    limiter.acquire()
    assert time.monotonic() - started >= 0.8, "the bucket did not pace"


def test_a_prompt_larger_than_the_whole_token_budget_does_not_deadlock():
    limiter = RateLimiter(LimiterConfig(input_tokens_per_minute=1000))
    limiter.acquire(estimated_input_tokens=10_000_000)  # returns rather than hangs


def test_a_released_slot_is_returned_even_when_the_request_raises():
    limiter = RateLimiter(LimiterConfig(max_concurrency=1))
    with pytest.raises(RuntimeError):
        with limiter.slot():
            raise RuntimeError("boom")
    with limiter.slot():  # would block forever if the permit leaked
        pass


def test_limits_are_off_unless_configured():
    assert limiter_from_env({}).config.active is False
    assert limiter_from_env({"CERTIVITY_LIMIT_CONCURRENCY": "0"}).config.active is False
    assert limiter_from_env(
        {"CERTIVITY_LIMIT_CONCURRENCY": "4", "CERTIVITY_LIMIT_REQUESTS_PER_MINUTE": "50"}
    ).config.max_concurrency == 4
