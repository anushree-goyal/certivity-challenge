"""Shared run state for the extraction component (owner: Anushree).

``RunContext`` carries the collaborators and the mutable bookkeeping that every
stage needs: usage totals, warnings, the event callback and the model-call
budget. Keeping it here means no stage reaches for a global, and the whole
component stays callable from a background worker without the API module.
"""

from __future__ import annotations

import datetime as _dt
import logging
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
    budget_exhausted: bool = False
    """Set once the model budget is hit, so the run finishes as partial rather
    than raising out of a half-processed corpus."""

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
        try:
            self.emit_event(event)
        except Exception:  # pragma: no cover - a UI problem is not a run problem
            logger.warning("emit_event raised for stage %s", stage, exc_info=True)

    def warn(self, message: str, *, stage: str = "agent") -> None:
        """Record a warning once, and surface it as an event."""
        if message not in self.warnings:
            self.warnings.append(message)
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
            self.budget_exhausted = True
            raise
        self.usage = self.usage.merged_with(response.usage)
        for warning in response.warnings:
            self.warn(f"{task}: {warning}", stage=task)
        return response
