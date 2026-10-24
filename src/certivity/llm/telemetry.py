"""Observable Anthropic API usage (owner: Anushree).

Why this exists: a run slowed from one completed section every 9.6 s to one
every 29 s and nobody could say why. "Probably rate limiting" was a guess, and
the three candidate causes - request-rate limits, token-rate limits and plain
model latency - are indistinguishable from the outside unless somebody counts.

The observation point is an httpx response hook rather than the SDK's return
value, because the SDK retries internally: one ``messages.create`` that returns
successfully may have been three HTTP round trips, two of them 429s. Only the
transport sees those. Every response the client receives passes through
``on_response``, retries included, so SDK-internal retries are counted even
when ``max_retries`` is left to the SDK.

Nothing here raises into a run. A telemetry failure must not fail extraction,
so the hook swallows its own errors.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Published Anthropic prices, USD per million tokens, for cost *estimates* only.
# A missing model falls back to None and the estimate is reported as unavailable
# rather than guessed, because a wrong cost number is worse than no number.
_PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _price_for(model: str) -> Optional[tuple[float, float]]:
    for prefix, price in _PRICES_PER_MTOK.items():
        if model.startswith(prefix):
            return price
    return None


@dataclass
class _Bucket:
    """Counters for one HTTP status class or failure mode."""

    count: int = 0
    retry_after_total_s: float = 0.0
    last_message: Optional[str] = None


@dataclass
class ApiTelemetry:
    """Thread-safe counters for one adapter's provider traffic.

    Every field is written from several worker threads, so all mutation goes
    through ``lock``. Reads take it too: a snapshot assembled from torn reads
    would report totals that never existed.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    started_at: float = field(default_factory=time.monotonic)

    # Calls, as the application sees them (one generate_json = one call).
    calls_attempted: int = 0
    calls_completed: int = 0
    calls_failed: int = 0
    cache_hits: int = 0
    schema_reasks: int = 0

    # HTTP round trips, as the transport sees them (includes every retry).
    http_requests: int = 0
    http_by_status: dict[int, int] = field(default_factory=dict)
    rate_limited: int = 0
    """Responses with HTTP 429."""
    overloaded: int = 0
    """Responses with HTTP 529, the provider's "try again" signal."""
    server_errors: int = 0
    retries_observed: int = 0
    """HTTP round trips beyond the first for one application call."""
    retry_after_s_total: float = 0.0
    retry_sleep_s_total: float = 0.0
    """Time this process actually spent waiting before a retry."""
    transport_failures: int = 0
    timeouts: int = 0

    # Tokens.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    # Latency, in seconds, of completed application calls.
    latencies: list[float] = field(default_factory=list)
    http_latencies: list[float] = field(default_factory=list)

    # Concurrency.
    in_flight: int = 0
    max_in_flight: int = 0

    by_task: dict[str, int] = field(default_factory=dict)
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)

    # The provider's own view of the limits, from the most recent response.
    limit_headers: dict[str, str] = field(default_factory=dict)

    failures: dict[str, _Bucket] = field(default_factory=dict)

    # -- transport-level observation --------------------------------------- #

    def on_response(self, response: Any) -> None:
        """httpx response hook. Sees every round trip, including SDK retries."""
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            if not status:
                # Not something this can say anything about. Counting it would
                # inflate the round-trip total that the retry ratio is computed
                # from, which is the number most likely to be read as evidence.
                return
            headers = getattr(response, "headers", {}) or {}
            elapsed = None
            try:
                elapsed = response.elapsed.total_seconds()
            except (RuntimeError, AttributeError):
                # httpx raises if the response body has not been read yet, which
                # is the normal case for a streamed response.
                elapsed = None
            retry_after = _retry_after_seconds(headers)
            limits = {
                k.lower(): v
                for k, v in headers.items()
                if k.lower().startswith("anthropic-ratelimit")
            }
            with self.lock:
                self.http_requests += 1
                self.http_by_status[status] = self.http_by_status.get(status, 0) + 1
                if elapsed is not None:
                    self.http_latencies.append(elapsed)
                if limits:
                    self.limit_headers.update(limits)
                if status == 429:
                    self.rate_limited += 1
                    if retry_after is not None:
                        self.retry_after_s_total += retry_after
                elif status == 529:
                    self.overloaded += 1
                elif status >= 500:
                    self.server_errors += 1
        except Exception:  # pragma: no cover - telemetry must never fail a run
            logger.debug("telemetry response hook failed", exc_info=True)

    # -- application-level observation ------------------------------------- #

    def call_started(self, task: str, model: str) -> None:
        with self.lock:
            self.calls_attempted += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self.by_task[task] = self.by_task.get(task, 0) + 1
            self.by_model.setdefault(model, {"calls": 0, "input": 0, "output": 0})
            self.by_model[model]["calls"] += 1

    def call_finished(
        self,
        *,
        model: str,
        latency_s: float,
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        cache_creation: Optional[int] = None,
        cache_read: Optional[int] = None,
        attempts: int = 1,
    ) -> None:
        with self.lock:
            self.in_flight = max(0, self.in_flight - 1)
            self.calls_completed += 1
            self.latencies.append(latency_s)
            self.retries_observed += max(0, attempts - 1)
            self.input_tokens += int(input_tokens or 0)
            self.output_tokens += int(output_tokens or 0)
            self.cache_creation_tokens += int(cache_creation or 0)
            self.cache_read_tokens += int(cache_read or 0)
            entry = self.by_model.setdefault(
                model, {"calls": 0, "input": 0, "output": 0}
            )
            entry["input"] += int(input_tokens or 0)
            entry["output"] += int(output_tokens or 0)

    def call_failed(self, kind: str, message: str) -> None:
        with self.lock:
            self.in_flight = max(0, self.in_flight - 1)
            self.calls_failed += 1
            bucket = self.failures.setdefault(kind, _Bucket())
            bucket.count += 1
            bucket.last_message = message[:300]
            if kind == "timeout":
                self.timeouts += 1
            elif kind == "connection":
                self.transport_failures += 1

    def cache_hit(self) -> None:
        with self.lock:
            self.cache_hits += 1

    def schema_reask(self) -> None:
        with self.lock:
            self.schema_reasks += 1

    def slept(self, seconds: float) -> None:
        with self.lock:
            self.retry_sleep_s_total += seconds

    # -- reporting ---------------------------------------------------------- #

    def snapshot(self) -> dict[str, Any]:
        """A consistent, JSON-safe view of everything measured so far."""
        with self.lock:
            elapsed = max(1e-9, time.monotonic() - self.started_at)
            latencies = sorted(self.latencies)
            http_latencies = sorted(self.http_latencies)
            cost = self._estimated_cost_usd()
            return {
                "elapsed_s": round(elapsed, 1),
                "calls": {
                    "attempted": self.calls_attempted,
                    "completed": self.calls_completed,
                    "failed": self.calls_failed,
                    "cache_hits": self.cache_hits,
                    "schema_reasks": self.schema_reasks,
                    "per_minute": round(self.calls_completed / elapsed * 60, 2),
                },
                "http": {
                    "requests": self.http_requests,
                    "by_status": dict(sorted(self.http_by_status.items())),
                    "rate_limited_429": self.rate_limited,
                    "overloaded_529": self.overloaded,
                    "server_errors_5xx": self.server_errors,
                    "retries_observed": self.retries_observed,
                    "retry_after_s_total": round(self.retry_after_s_total, 1),
                    "retry_sleep_s_total": round(self.retry_sleep_s_total, 1),
                    "timeouts": self.timeouts,
                    "transport_failures": self.transport_failures,
                    "requests_per_call": (
                        round(self.http_requests / self.calls_attempted, 2)
                        if self.calls_attempted
                        else None
                    ),
                },
                "latency_s": _percentiles(latencies),
                "http_latency_s": _percentiles(http_latencies),
                "tokens": {
                    "input": self.input_tokens,
                    "output": self.output_tokens,
                    "cache_creation": self.cache_creation_tokens,
                    "cache_read": self.cache_read_tokens,
                    "input_per_minute": round(self.input_tokens / elapsed * 60),
                    "output_per_minute": round(self.output_tokens / elapsed * 60),
                },
                "concurrency": {
                    "in_flight": self.in_flight,
                    "max_in_flight": self.max_in_flight,
                },
                "by_task": dict(sorted(self.by_task.items())),
                "by_model": {k: dict(v) for k, v in sorted(self.by_model.items())},
                "provider_limit_headers": dict(sorted(self.limit_headers.items())),
                "failures": {
                    k: {"count": v.count, "last": v.last_message}
                    for k, v in sorted(self.failures.items())
                },
                "estimated_cost_usd": cost,
            }

    def _estimated_cost_usd(self) -> Optional[float]:
        """Caller holds the lock."""
        total = 0.0
        known = False
        for model, counts in self.by_model.items():
            price = _price_for(model)
            if price is None:
                continue
            known = True
            total += counts["input"] / 1e6 * price[0]
            total += counts["output"] / 1e6 * price[1]
        return round(total, 4) if known else None

    def diagnosis(self) -> list[str]:
        """Plain statements about what the numbers show, with no speculation.

        Each line is only emitted when the measurement supports it. The point is
        to separate the three causes that look identical from the outside:
        request-rate limiting, token-rate limiting and ordinary model latency.
        """
        snap = self.snapshot()
        http, calls, lat = snap["http"], snap["calls"], snap["latency_s"]
        out: list[str] = []

        if http["requests"] == 0:
            return ["no provider request was made (every call was served from cache)"]

        share_429 = http["rate_limited_429"] / http["requests"]
        if http["rate_limited_429"] == 0:
            out.append(
                f"no HTTP 429 in {http['requests']} request(s): request-rate and "
                "token-rate limiting are not what this run is waiting on"
            )
        else:
            out.append(
                f"{http['rate_limited_429']} of {http['requests']} requests "
                f"({share_429:.1%}) were rate limited; the provider asked for "
                f"{http['retry_after_s_total']:.0f}s of waiting in total"
            )
            headers = snap["provider_limit_headers"]
            for name, label in (
                ("anthropic-ratelimit-requests-remaining", "request-rate"),
                ("anthropic-ratelimit-input-tokens-remaining", "input-token-rate"),
                ("anthropic-ratelimit-output-tokens-remaining", "output-token-rate"),
            ):
                if name in headers:
                    out.append(f"  {label} remaining at last response: {headers[name]}")

        if http["timeouts"]:
            out.append(
                f"{http['timeouts']} call(s) exceeded the request timeout; a timeout "
                "is model latency against a client deadline, not throttling"
            )
        if lat.get("p50") is not None:
            out.append(
                f"model latency p50 {lat['p50']}s, p90 {lat['p90']}s, "
                f"max {lat['max']}s over {calls['completed']} completed call(s)"
            )
        if http["requests_per_call"] and http["requests_per_call"] > 1.2:
            out.append(
                f"{http['requests_per_call']} HTTP round trips per application call: "
                "retries are consuming throughput"
            )
        if snap["tokens"]["output"] and calls["completed"]:
            out.append(
                f"mean output {snap['tokens']['output'] // calls['completed']} tokens "
                f"and mean input {snap['tokens']['input'] // calls['completed']} tokens "
                "per call"
            )
        if snap["concurrency"]["max_in_flight"]:
            out.append(
                f"peak concurrency {snap['concurrency']['max_in_flight']}; throughput "
                f"{calls['per_minute']} completed call(s) per minute"
            )
        return out


def _percentiles(values: list[float]) -> dict[str, Optional[float]]:
    if not values:
        return {"n": 0, "p50": None, "p90": None, "max": None, "mean": None}

    def at(fraction: float) -> float:
        index = min(len(values) - 1, int(fraction * len(values)))
        return round(values[index], 1)

    return {
        "n": len(values),
        "p50": at(0.5),
        "p90": at(0.9),
        "max": round(values[-1], 1),
        "mean": round(sum(values) / len(values), 1),
    }


def _retry_after_seconds(headers: Any) -> Optional[float]:
    """Read the provider's own backoff guidance, preferring the ms variant."""
    try:
        ms = headers.get("retry-after-ms")
        if ms is not None:
            return float(ms) / 1000.0
        seconds = headers.get("retry-after")
        if seconds is not None:
            return float(seconds)
    except (TypeError, ValueError, AttributeError):
        return None
    return None
