"""The shared structured-output / vision adapter (owner: Anushree).

One boundary, used by the extraction component and - with Teammate 3's own
prompt text - by review. Nothing else in the application talks to a provider,
and the browser never does.

What this module guarantees to its callers:

* ``generate_json`` returns parsed data that conformed to ``schema``, or raises.
  It never returns a half-parsed or invented object.
* Missing credentials raise ``LLMConfigurationError`` immediately. The
  application must not fall back to fabricated output when the model is
  unavailable, so there is no silent degradation path.
* Usage, latency, provider and model identity are measured and returned, so a
  run record can state what actually produced it.
* Calls are bounded by a per-run budget, a request timeout and a retry limit.

Valid JSON is not evidence of correct content. Callers still verify every
obligation against source passages; that check lives in the agent and quality
components, not here.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional, Sequence

from certivity.contracts import (
    ImagePayload,
    LLMCallError,
    LLMConfigurationError,
    LLMResponse,
    ModelBudgetExceeded,
    ModelUsage,
    content_hash,
)
from certivity.llm.cache import CacheBackend, CallCache, NullCache
from certivity.llm.schema_check import schema_errors

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
"""Opus 5 is the default. A per-task override can select a cheaper model for
high-volume tasks once a run is reliable; see ``RunConfig.model_overrides``."""

FALLBACK_BETA = "server-side-fallback-2026-07-01"
"""Server-side refusal fallback. A policy decline would otherwise end a run
mid-corpus, so this is on by default and disabled only by configuration."""

MAX_IMAGE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class ProviderConfig:
    """Everything that can change a model answer, in one hashable record.

    This record is part of the model-call cache key, so adding a field here
    automatically invalidates cached results that were produced without it.
    """

    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    max_output_tokens: int = 16000
    effort: Optional[str] = "high"
    """``low`` | ``medium`` | ``high`` | ``xhigh`` | ``max``, or None to omit."""
    timeout_s: float = 120.0
    max_retries: int = 2
    """Transport retries, handled by the SDK. Schema reasks are counted separately."""
    max_schema_reasks: int = 1
    use_refusal_fallbacks: bool = True
    cache_prompt_prefix: bool = True
    """Mark the task instructions cacheable; they repeat across every section."""
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: Optional[str] = None

    def identity(self) -> dict[str, Any]:
        """Fields that affect the answer, for the cache key and the run record."""
        return {
            "provider": self.provider,
            "model": self.model,
            "max_output_tokens": self.max_output_tokens,
            "effort": self.effort,
        }


@dataclass
class _Budget:
    """Per-run call ceiling, shared across threads inside one adapter."""

    max_calls: Optional[int] = None
    calls: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def claim(self) -> None:
        with self.lock:
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise ModelBudgetExceeded(
                    f"run budget of {self.max_calls} model calls is exhausted"
                )
            self.calls += 1


def _encode_image(image: ImagePayload) -> tuple[str, str]:
    """Return (media_type, base64 data), reading from disk when needed."""
    if image.data_base64:
        return image.media_type, image.data_base64
    assert image.asset_ref is not None  # guaranteed by ImagePayload.__post_init__
    path = Path(image.asset_ref)
    if not path.is_file():
        raise LLMCallError(f"image asset not found: {image.asset_ref}")
    raw = path.read_bytes()
    if len(raw) > MAX_IMAGE_BYTES:
        raise LLMCallError(
            f"image {path.name} is {len(raw)} bytes, over the {MAX_IMAGE_BYTES} limit"
        )
    media_type = image.media_type or mimetypes.guess_type(path.name)[0] or "image/png"
    return media_type, base64.standard_b64encode(raw).decode("ascii")


class StructuredLLMAdapter:
    """Implements ``contracts.LLMAdapter`` against the Anthropic Messages API."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        *,
        cache: CacheBackend | None = None,
        max_calls: Optional[int] = None,
        model_overrides: dict[str, str] | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or ProviderConfig()
        self.cache: CacheBackend = cache or NullCache()
        self.model_overrides = dict(model_overrides or {})
        self._budget = _Budget(max_calls=max_calls)
        self._client = client
        self._client_lock = threading.Lock()
        self._fallbacks_available = self.config.use_refusal_fallbacks

    # -- configuration ---------------------------------------------------- #

    def describe(self) -> dict[str, Any]:
        """Configuration readiness without secrets, for ``GET /api/health``."""
        return {
            "provider": self.config.provider,
            "model": self.config.model,
            "effort": self.config.effort,
            "credentials_configured": self.credentials_available(),
            "credential_source": self._credential_source(),
            "refusal_fallbacks": self._fallbacks_available,
            "cache": type(self.cache).__name__,
            "calls_used": self._budget.calls,
            "max_calls": self._budget.max_calls,
            "model_overrides": self.model_overrides,
        }

    def credentials_available(self) -> bool:
        return self._credential_source() is not None

    def _credential_source(self) -> Optional[str]:
        """Which credential the SDK will use, without reading its value.

        An unset ``ANTHROPIC_API_KEY`` does not mean there are no credentials:
        the SDK also accepts ``ANTHROPIC_AUTH_TOKEN`` and an ``ant auth login``
        profile on disk.
        """
        if os.environ.get(self.config.api_key_env):
            return self.config.api_key_env
        if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return "ANTHROPIC_AUTH_TOKEN"
        profile_dir = Path.home() / ".config" / "anthropic"
        if profile_dir.is_dir() and any(profile_dir.iterdir()):
            return "ant-auth-profile"
        return None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise LLMConfigurationError(
                    "the 'anthropic' package is not installed; it is a declared "
                    "dependency of the agent component (see docs/agent/dependencies.md)"
                ) from exc
            if not self.credentials_available():
                raise LLMConfigurationError(
                    f"no model credentials found: set {self.config.api_key_env} or "
                    "ANTHROPIC_AUTH_TOKEN, or run 'ant auth login'. The application "
                    "does not produce requirements without a configured model."
                )
            kwargs: dict[str, Any] = {
                "timeout": self.config.timeout_s,
                "max_retries": self.config.max_retries,
            }
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._client = anthropic.Anthropic(**kwargs)
            return self._client

    def model_for(self, task: str) -> str:
        return self.model_overrides.get(task, self.config.model)

    # -- the single public call ------------------------------------------- #

    def generate_json(
        self,
        task: str,
        instructions: str,
        payload: Any,
        schema: dict[str, Any],
        images: Sequence[ImagePayload] | None = None,
        *,
        prompt_version: str = "v1",
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        """Run one structured call and return schema-conforming parsed data.

        ``task`` names the call site. It selects any per-task model override and
        groups usage in the run record, so a cost question can be answered per
        stage rather than per run.
        """
        model = self.model_for(task)
        images = tuple(images or ())
        encoded = [_encode_image(image) for image in images]
        config = self.config if max_output_tokens is None else replace(
            self.config, max_output_tokens=max_output_tokens
        )

        key = content_hash(
            "certivity-llm-call-v1",
            task,
            prompt_version,
            instructions,
            json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str),
            json.dumps(schema, sort_keys=True),
            [(media_type, content_hash(data)) for media_type, data in encoded],
            config.identity(),
        )

        cached = self.cache.get(key)
        if cached is not None:
            return LLMResponse(
                data=cached["data"],
                provider=cached.get("provider", config.provider),
                model=cached.get("model", model),
                task=task,
                latency_ms=0.0,
                usage=ModelUsage(cached_calls=1, by_model={model: 0}),
                warnings=tuple(cached.get("warnings", ())),
                cached=True,
                finish_reason=cached.get("finish_reason"),
            )

        response = self._call_with_reasks(
            task=task,
            model=model,
            config=config,
            instructions=instructions,
            payload=payload,
            schema=schema,
            encoded_images=encoded,
            image_captions=[image.caption for image in images],
            prompt_version=prompt_version,
        )
        self.cache.put(
            key,
            {
                "data": response.data,
                "provider": response.provider,
                "model": response.model,
                "warnings": list(response.warnings),
                "finish_reason": response.finish_reason,
            },
        )
        return response

    # -- internals -------------------------------------------------------- #

    def _call_with_reasks(
        self,
        *,
        task: str,
        model: str,
        config: ProviderConfig,
        instructions: str,
        payload: Any,
        schema: dict[str, Any],
        encoded_images: list[tuple[str, str]],
        image_captions: list[Optional[str]],
        prompt_version: str,
    ) -> LLMResponse:
        """Call, then reask once if the parsed output violates the schema.

        A schema violation is a different failure from a transport error: the
        SDK already retried the latter. Reasking quotes the violations back so
        the correction is specific rather than a blind repeat.
        """
        correction: Optional[str] = None
        warnings: list[str] = []
        last_errors: list[str] = []

        for attempt in range(config.max_schema_reasks + 1):
            raw, meta = self._one_request(
                task=task,
                model=model,
                config=config,
                instructions=instructions,
                payload=payload,
                schema=schema,
                encoded_images=encoded_images,
                image_captions=image_captions,
                correction=correction,
            )
            warnings.extend(meta.pop("warnings", []))

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                last_errors = [f"response was not valid JSON: {exc}"]
            else:
                last_errors = schema_errors(schema, data)
                if not last_errors:
                    return LLMResponse(
                        data=data,
                        provider=config.provider,
                        model=meta["model"],
                        task=task,
                        latency_ms=meta["latency_ms"],
                        usage=meta["usage"],
                        warnings=tuple(warnings),
                        cached=False,
                        raw_text=raw if meta.get("keep_raw") else None,
                        finish_reason=meta.get("finish_reason"),
                    )

            warnings.append(
                f"attempt {attempt + 1} violated the schema: {'; '.join(last_errors[:5])}"
            )
            correction = (
                "Your previous reply did not satisfy the required output schema:\n"
                + "\n".join(f"- {err}" for err in last_errors[:10])
                + "\nReturn corrected JSON for the same task. Do not invent values to "
                "satisfy a field: use null or an empty list where the source does not "
                "establish a value."
            )

        raise LLMCallError(
            f"task {task!r} on {model}: output still violated the schema after "
            f"{config.max_schema_reasks + 1} attempts: {'; '.join(last_errors[:5])}"
        )

    def _one_request(
        self,
        *,
        task: str,
        model: str,
        config: ProviderConfig,
        instructions: str,
        payload: Any,
        schema: dict[str, Any],
        encoded_images: list[tuple[str, str]],
        image_captions: list[Optional[str]],
        correction: Optional[str],
    ) -> tuple[str, dict[str, Any]]:
        import anthropic

        client = self._get_client()
        self._budget.claim()

        system: Any = instructions
        if config.cache_prompt_prefix:
            # Task instructions repeat across every section of every document,
            # so they are the stable cacheable prefix; the payload stays volatile.
            system = [
                {
                    "type": "text",
                    "text": instructions,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        content: list[dict[str, Any]] = []
        for (media_type, data), caption in zip(encoded_images, image_captions):
            if caption:
                content.append({"type": "text", "text": f"Image: {caption}"})
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                }
            )
        body = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False, indent=1, default=str
        )
        content.append({"type": "text", "text": body})
        if correction:
            content.append({"type": "text", "text": correction})

        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": schema}
        }
        if config.effort:
            output_config["effort"] = config.effort

        request: dict[str, Any] = {
            "model": model,
            "max_tokens": config.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
            "output_config": output_config,
        }

        warnings: list[str] = []
        started = time.monotonic()
        use_fallbacks = self._fallbacks_available
        try:
            response = self._send(client, request, use_fallbacks)
        except anthropic.BadRequestError as exc:
            message = str(exc)
            if use_fallbacks and ("fallback" in message or "beta" in message):
                # The deployment does not accept the refusal-fallback beta.
                # Degrade once, loudly, rather than failing the whole run.
                self._fallbacks_available = False
                warnings.append(
                    "server-side refusal fallbacks rejected by this deployment; "
                    "retried without them"
                )
                response = self._send(client, request, False)
            else:
                raise LLMCallError(f"task {task!r}: request rejected: {message}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMCallError(
                f"task {task!r}: provider returned {exc.status_code}: {exc}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMCallError(f"task {task!r}: could not reach the provider: {exc}") from exc
        latency_ms = (time.monotonic() - started) * 1000.0

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMCallError(
                f"task {task!r}: the model declined this request "
                f"(category={getattr(details, 'category', None)}). No requirement was "
                "produced for it."
            )
        if stop_reason == "max_tokens":
            warnings.append(
                "response hit max_tokens and may be truncated; raise "
                "ProviderConfig.max_output_tokens or shrink the batch"
            )

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        usage = _usage_from_response(response, model)
        return text, {
            "model": getattr(response, "model", model) or model,
            "latency_ms": latency_ms,
            "usage": usage,
            "warnings": warnings,
            "finish_reason": stop_reason,
            "keep_raw": False,
        }

    def _send(self, client: Any, request: dict[str, Any], use_fallbacks: bool) -> Any:
        if use_fallbacks:
            return client.beta.messages.create(
                **request, betas=[FALLBACK_BETA], fallbacks="default"
            )
        return client.messages.create(**request)


def _usage_from_response(response: Any, model: str) -> ModelUsage:
    """Read measured token usage, leaving counts None when unreported."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return ModelUsage(calls=1, by_model={model: 1})
    input_tokens = getattr(usage, "input_tokens", None)
    cache_read = getattr(usage, "cache_read_input_tokens", None) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", None) or 0
    if input_tokens is not None:
        input_tokens = input_tokens + cache_read + cache_write
    return ModelUsage(
        calls=1,
        input_tokens=input_tokens,
        output_tokens=getattr(usage, "output_tokens", None),
        by_model={getattr(response, "model", model) or model: 1},
    )


# --------------------------------------------------------------------------- #
# Construction helpers used by Teammate 4's composition layer
# --------------------------------------------------------------------------- #


def adapter_from_env(
    *,
    cache_dir: str | os.PathLike[str] | None = None,
    max_calls: Optional[int] = None,
    model_overrides: dict[str, str] | None = None,
    timeout_s: float | None = None,
    max_retries: int | None = None,
) -> StructuredLLMAdapter:
    """Build the adapter from environment configuration.

    Recognised variables: ``CERTIVITY_MODEL``, ``CERTIVITY_MODEL_EFFORT``,
    ``CERTIVITY_MAX_OUTPUT_TOKENS``, ``CERTIVITY_DISABLE_REFUSAL_FALLBACKS``,
    ``ANTHROPIC_BASE_URL``. Credentials are resolved by the SDK and are never
    read into this process's own records.
    """
    config = ProviderConfig(
        model=os.environ.get("CERTIVITY_MODEL", DEFAULT_MODEL),
        effort=os.environ.get("CERTIVITY_MODEL_EFFORT", "high") or None,
        max_output_tokens=int(os.environ.get("CERTIVITY_MAX_OUTPUT_TOKENS", "16000")),
        timeout_s=timeout_s if timeout_s is not None else 120.0,
        max_retries=max_retries if max_retries is not None else 2,
        use_refusal_fallbacks=not os.environ.get("CERTIVITY_DISABLE_REFUSAL_FALLBACKS"),
        base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
    )
    cache: CacheBackend = CallCache(cache_dir) if cache_dir else NullCache()
    return StructuredLLMAdapter(
        config,
        cache=cache,
        max_calls=max_calls,
        model_overrides=model_overrides,
    )


def describe_configuration(adapter: StructuredLLMAdapter | None = None) -> dict[str, Any]:
    """Health-check payload. Safe to serve: it contains no secret values."""
    if adapter is None:
        adapter = StructuredLLMAdapter()
    return adapter.describe()
