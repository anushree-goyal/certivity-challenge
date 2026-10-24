"""One rate limiter shared by every worker (owner: Anushree).

Eight extraction workers each deciding independently when to call the provider
is eight clients competing for one account's quota: they arrive together, get
429s together, back off together and then arrive together again. The fix is a
single gate in front of the provider that all of them pass through.

Three independent controls, because the provider enforces three different
limits and hitting one does not mean the others are near:

* ``max_concurrency`` - how many requests may be open at once.
* ``requests_per_minute`` - the request-rate limit.
* ``input_tokens_per_minute`` - the input-token-rate limit, estimated from the
  prompt before it is sent, since the real count is only known afterwards.

All three default to off. Turning a limiter on is a measurement-driven
decision, and an unmeasured limit is as likely to throttle a healthy run as to
protect it, so nothing here changes behaviour until it is configured.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional


class _TokenBucket:
    """Classic token bucket: ``rate`` units per minute, burst up to ``capacity``.

    ``acquire`` blocks until the units are available and returns how long it
    waited, so the caller can record the delay as measured evidence rather than
    an assumption.
    """

    def __init__(self, rate_per_minute: float, capacity: Optional[float] = None) -> None:
        self.rate_per_s = max(1e-9, rate_per_minute / 60.0)
        self.capacity = capacity if capacity is not None else rate_per_minute
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, units: float) -> float:
        # A single request larger than the whole bucket would wait forever, so it
        # is clamped: the limiter must not deadlock on an oversized prompt.
        units = max(0.0, min(float(units), self.capacity))
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate_per_s
                )
                self._updated = now
                if self._tokens >= units:
                    self._tokens -= units
                    return waited
                shortfall = units - self._tokens
                delay = shortfall / self.rate_per_s
            # Slept outside the lock so other threads can keep draining.
            delay = min(delay, 5.0)
            time.sleep(delay)
            waited += delay


@dataclass(frozen=True)
class LimiterConfig:
    max_concurrency: Optional[int] = None
    requests_per_minute: Optional[float] = None
    input_tokens_per_minute: Optional[float] = None

    @property
    def active(self) -> bool:
        return any(
            v is not None
            for v in (
                self.max_concurrency,
                self.requests_per_minute,
                self.input_tokens_per_minute,
            )
        )


class RateLimiter:
    """Shared gate. ``RateLimiter(LimiterConfig())`` is a no-op pass-through."""

    def __init__(self, config: LimiterConfig | None = None) -> None:
        self.config = config or LimiterConfig()
        self._semaphore = (
            threading.BoundedSemaphore(self.config.max_concurrency)
            if self.config.max_concurrency
            else None
        )
        self._requests = (
            _TokenBucket(self.config.requests_per_minute)
            if self.config.requests_per_minute
            else None
        )
        self._input_tokens = (
            _TokenBucket(self.config.input_tokens_per_minute)
            if self.config.input_tokens_per_minute
            else None
        )
        self._wait_lock = threading.Lock()
        self.total_wait_s = 0.0
        self.admitted = 0

    def describe(self) -> dict[str, object]:
        return {
            "max_concurrency": self.config.max_concurrency,
            "requests_per_minute": self.config.requests_per_minute,
            "input_tokens_per_minute": self.config.input_tokens_per_minute,
            "admitted": self.admitted,
            "total_wait_s": round(self.total_wait_s, 1),
        }

    def acquire(self, estimated_input_tokens: int = 0) -> float:
        """Block until this request may go out. Returns the seconds waited."""
        waited = 0.0
        if self._requests is not None:
            waited += self._requests.acquire(1)
        if self._input_tokens is not None and estimated_input_tokens:
            waited += self._input_tokens.acquire(estimated_input_tokens)
        if self._semaphore is not None:
            started = time.monotonic()
            self._semaphore.acquire()
            waited += time.monotonic() - started
        with self._wait_lock:
            self.total_wait_s += waited
            self.admitted += 1
        return waited

    def release(self) -> None:
        if self._semaphore is not None:
            try:
                self._semaphore.release()
            except ValueError:  # pragma: no cover - release without acquire
                pass

    # Used as a context manager so a raising request cannot leak a permit.
    class _Slot:
        def __init__(self, limiter: "RateLimiter", estimated_input_tokens: int) -> None:
            self._limiter = limiter
            self._estimate = estimated_input_tokens
            self.waited = 0.0

        def __enter__(self) -> "RateLimiter._Slot":
            self.waited = self._limiter.acquire(self._estimate)
            return self

        def __exit__(self, *exc: object) -> None:
            self._limiter.release()

    def slot(self, estimated_input_tokens: int = 0) -> "RateLimiter._Slot":
        return RateLimiter._Slot(self, estimated_input_tokens)


def limiter_from_env(env: dict[str, str] | None = None) -> RateLimiter:
    """Build the shared limiter from ``CERTIVITY_LIMIT_*``, off unless set."""
    import os

    source = env if env is not None else os.environ

    def number(name: str) -> Optional[float]:
        raw = source.get(name)
        if raw is None or not str(raw).strip():
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    concurrency = number("CERTIVITY_LIMIT_CONCURRENCY")
    return RateLimiter(
        LimiterConfig(
            max_concurrency=int(concurrency) if concurrency else None,
            requests_per_minute=number("CERTIVITY_LIMIT_REQUESTS_PER_MINUTE"),
            input_tokens_per_minute=number("CERTIVITY_LIMIT_INPUT_TOKENS_PER_MINUTE"),
        )
    )
