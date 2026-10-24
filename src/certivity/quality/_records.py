"""Construction of shared output records.

TEMPORARY FALLBACKS: if certivity.contracts is importable its records are used;
otherwise the minimal stand-ins below are. All construction goes through the make_*
helpers so a signature mismatch with the real contracts is fixed in this file only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:  # pragma: no cover - depends on Anushree's contracts landing
    from certivity.contracts import (  # type: ignore
        QualityReport, ReviewFinding, ValidationReport,
    )
    USING_SHARED_CONTRACTS = True
except Exception:  # noqa: BLE001
    USING_SHARED_CONTRACTS = False

    @dataclass
    class ReviewFinding:  # fallback
        draft_id: str | None
        category: str
        explanation: str
        supporting_passage_ids: list[str] = field(default_factory=list)
        requested_context: str | None = None

    @dataclass
    class QualityReport:  # fallback
        accepted_draft_ids: list[str]
        findings: list[ReviewFinding]
        duplicate_mapping: dict[str, str]
        rejected: dict[str, str]
        remaining_issues: list[ReviewFinding]

    @dataclass
    class ValidationError:  # fallback
        path: str
        message: str

    @dataclass
    class ValidationReport:  # fallback
        valid: bool
        errors: list[ValidationError]
        official_validator_ran: bool
        official_validator_output: str | None = None
        checked_schema: str | None = None


def make_finding(draft_id, category, explanation, supporting=None, requested=None):
    return ReviewFinding(
        draft_id=draft_id,
        category=category,
        explanation=explanation,
        supporting_passage_ids=list(supporting or []),
        requested_context=requested,
    )


def make_report(accepted, findings, duplicate_mapping, rejected, remaining):
    return QualityReport(
        accepted_draft_ids=list(accepted),
        findings=list(findings),
        duplicate_mapping=dict(duplicate_mapping),
        rejected=dict(rejected),
        remaining_issues=list(remaining),
    )


def make_validation_error(path: str, message: str):
    if USING_SHARED_CONTRACTS:  # pragma: no cover
        return {"path": path, "message": message}
    return ValidationError(path=path, message=message)  # type: ignore[name-defined]


def make_validation_report(valid, errors, official_ran, official_output, schema):
    return ValidationReport(
        valid=valid,
        errors=list(errors),
        official_validator_ran=official_ran,
        official_validator_output=official_output,
        checked_schema=schema,
    )


def emit(emit_event: Any, stage: str, message: str, completed=None, total=None) -> None:
    """RunEvent construction is unknown until contracts land; send a plain dict."""
    if emit_event is None:
        return
    emit_event({"stage": stage, "message": message, "completed": completed, "total": total})
