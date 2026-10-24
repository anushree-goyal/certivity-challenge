"""Shared run state for the extraction component (owner: Anushree).

``RunContext`` carries the collaborators and the mutable bookkeeping that every
stage needs: usage totals, warnings, the event callback and the model-call
budget. Keeping it here means no stage reaches for a global, and the whole
component stays callable from a background worker without the API module.
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from certivity.contracts import (
    CorpusReader,
    EmitEvent,
    ImagePayload,
    LLMAdapter,
    LLMResponse,
    ModelBudgetExceeded,
    ModelUsage,
    RunConfig,
    RunEvent,
    RunInput,
    null_emit,
)

logger = logging.getLogger(__name__)


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class RunContext:
    run_input: RunInput
    corpus: CorpusReader
    llm: LLMAdapter
    emit_event: EmitEvent = null_emit
    usage: ModelUsage = field(default_factory=ModelUsage)
    warnings: list[str] = field(default_factory=list)
    economy: bool = False
    """True once the run makes no more model calls (economy mode, or the model
    became unusable in "auto" mode). Read by every stage that would call it."""
    budget_exhausted: bool = False
    """Set once the model budget is hit, so the run finishes as partial rather
    than raising out of a half-processed corpus."""
    lock: threading.Lock = field(default_factory=threading.Lock)
    """Guards usage, warnings and budget_exhausted.

    Sections are extracted concurrently, so these are written from several
    worker threads. Without the lock, usage totals would be lost to
    read-modify-write races and the warning list could interleave."""

    @property
    def config(self) -> RunConfig:
        return self.run_input.config

    # -- reporting -------------------------------------------------------- #

    def event(
        self,
        stage: str,
        message: str,
        *,
        completed: Optional[int] = None,
        total: Optional[int] = None,
        affected_ids: Sequence[str] = (),
        level: str = "info",
    ) -> None:
        """Report progress. A failing callback must not fail the run."""
        event = RunEvent(
            stage=stage,
            message=message,
            timestamp=utc_now(),
            completed=completed,
            total=total,
            affected_ids=tuple(affected_ids),
            level=level,
        )
        # Deliberately NOT under self.lock. emit_event is the caller's code: it
        # writes to the application's run store, which has a lock of its own.
        # Holding this lock across that call inverted the lock order against a
        # worker that took the store's lock and then called warn(), and the
        # first full run deadlocked after about 45 minutes - alive, sleeping,
        # no progress. A lock may guard this object's own state and nothing
        # else.
        try:
            self.emit_event(event)
        except Exception:  # pragma: no cover - a UI problem is not a run problem
            logger.warning("emit_event raised for stage %s", stage, exc_info=True)

    def warn(self, message: str, *, stage: str = "agent") -> None:
        """Record a warning once, and surface it as an event."""
        with self.lock:
            if message in self.warnings:
                return
            self.warnings.append(message)
        # event() is called after releasing the lock, for the same reason.
        self.event(stage, message, level="warning")

    # -- model calls ------------------------------------------------------ #

    def call_json(
        self,
        task: str,
        instructions: str,
        payload: Any,
        schema: dict[str, Any],
        *,
        prompt_version: str,
        images: Sequence[ImagePayload] | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        """One structured model call, with usage accumulated into the run.

        ``ModelBudgetExceeded`` is re-raised: a caller that can continue with
        partial coverage catches it, and a caller that cannot lets it travel.
        """
        try:
            response = self.llm.generate_json(
                task,
                instructions,
                payload,
                schema,
                images,
                prompt_version=prompt_version,
                max_output_tokens=max_output_tokens,
            )
        except ModelBudgetExceeded:
            with self.lock:
                self.budget_exhausted = True
            raise
        with self.lock:
            self.usage = self.usage.merged_with(response.usage)
        for warning in response.warnings:
            self.warn(f"{task}: {warning}", stage=task)
        return response
