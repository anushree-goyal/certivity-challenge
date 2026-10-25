"""The only place that talks to the model: one structured-JSON call, cached on disk by request content."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from certivity.config import settings

# List prices in USD per million tokens (input, output), for the run's cost estimate only.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00), "claude-opus-5-5": (4.00, 20.00)}
# Sonnet 5.5 accepts the server-side refusal fallback; Haiku 5.5 has none.
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5"}


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_model: dict[str, int] = field(default_factory=dict)
    tokens_by_model: dict[str, list[int]] = field(default_factory=dict)  # model -> [input, output]
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def cost_usd(self) -> float:
        return round(sum(i * PRICES.get(m, (0, 0))[0] / 1e6 + o * PRICES.get(m, (0, 0))[1] / 1e6
                         for m, (i, o) in self.tokens_by_model.items()), 4)

    def as_dict(self) -> dict:
        return {**{k: v for k, v in self.__dict__.items() if not k.startswith("_")}, "cost_usd": self.cost_usd}


def credentials_configured() -> bool:
    return any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))


def image_block(path: Path) -> dict:
    media_type = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


class LLM:
    def __init__(self) -> None:
        self.client = anthropic.Anthropic(max_retries=4)
        self.usage = Usage()
        self.cache_dir = settings.cache_dir / "llm"

    def json(self, *, model: str, system: str, content: list[dict], schema: dict, effort: str = "low") -> dict:
        request = {
            "model": model,
            "max_tokens": 16000,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": content}],
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        }
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        cached = self.cache_dir / f"{key}.json"
        if cached.is_file():
            with self.usage._lock:
                self.usage.cached_calls += 1
            return json.loads(cached.read_text())

        if model in FALLBACK_MODELS:
            response = self.client.beta.messages.create(
                **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        else:
            response = self.client.messages.create(**request)
        if response.stop_reason == "refusal":
            raise LLMError(f"{model} declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"{model} ran out of output tokens")
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)

        with self.usage._lock:
            self.usage.calls += 1
            self.usage.input_tokens += response.usage.input_tokens
            self.usage.output_tokens += response.usage.output_tokens
            self.usage.by_model[model] = self.usage.by_model.get(model, 0) + 1
            used = self.usage.tokens_by_model.setdefault(model, [0, 0])
            u = response.usage
            used[0] += u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)
            used[1] += u.output_tokens
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data))
        return data
