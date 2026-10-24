"""Construction of shared output records (certivity.contracts 1.1.0).

All construction goes through the make_* helpers so a contract change is absorbed here.

Finding codes: this module keeps fine-grained codes (e.g. ``page_unverified``) because they
drive the reject/revise logic and tell the UI exactly what went wrong. The contract has a
coarser ``FindingCategory`` and no code field, so each code maps to a (category, severity)
pair below and the code is kept as an ``[code]`` prefix of ``explanation``; ``finding_code``
reads it back. Proposed to Anushree: a ``ReviewFinding.code`` field, like ValidationError.code.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from certivity.contracts import (FindingCategory as C, FindingSeverity as S, ModelUsage,
                                 QualityReport, ReviewFinding, ValidationError,
                                 ValidationReport, make_event)

# code -> (contract category, severity)
CODE_MAP: dict[str, tuple[C, S]] = {
    # deterministic
    "source_missing": (C.UNRESOLVED_SOURCE, S.BLOCKING),
    "wrong_version_or_document": (C.WRONG_SOURCE_VERSION, S.BLOCKING),
    "snapshot_mismatch": (C.WRONG_SOURCE_VERSION, S.BLOCKING),
    "supporting_passage_invalid": (C.UNRESOLVED_SOURCE, S.REVISION_REQUESTED),
    "page_unverified": (C.UNRESOLVED_SOURCE, S.REVISION_REQUESTED),
    "no_primary_passage": (C.UNRESOLVED_SOURCE, S.REVISION_REQUESTED),
    "empty_text": (C.OTHER, S.REVISION_REQUESTED),
    "text_too_long": (C.TOO_LONG, S.REVISION_REQUESTED),
    "duplicate_draft_id": (C.OTHER, S.REVISION_REQUESTED),
    # heuristic flags for semantic review: informational, never a revision trigger on their own
    "numeric_not_in_evidence": (C.NUMERIC_MISMATCH, S.INFORMATIONAL),
    "possible_lost_condition": (C.MISSING_CONDITION, S.INFORMATIONAL),
    "primary_spans_pages": (C.OTHER, S.INFORMATIONAL),
    "primary_is_context_passage": (C.UNSUPPORTED_BY_SOURCE, S.INFORMATIONAL),
    "unresolved_reference": (C.UNRESOLVED_SOURCE, S.INFORMATIONAL),
    # semantic
    "semantic_supported": (C.OTHER, S.INFORMATIONAL),
    "semantic_unsupported": (C.UNSUPPORTED_BY_SOURCE, S.BLOCKING),
    "semantic_needs_revision": (C.UNSUPPORTED_BY_SOURCE, S.REVISION_REQUESTED),
    "semantic_needs_context": (C.CONTEXT_REQUESTED, S.REVISION_REQUESTED),
    "semantic_unreviewed": (C.OTHER, S.INFORMATIONAL),
    # dedup
    "duplicate_removed": (C.DUPLICATE, S.INFORMATIONAL),
    "possible_duplicate_unresolved": (C.DUPLICATE, S.INFORMATIONAL),
    "duplicate_candidate_kept_distinct": (C.DUPLICATE, S.INFORMATIONAL),
    # run level
    "review_incomplete": (C.OTHER, S.INFORMATIONAL),
}

_CODE_RE = re.compile(r"^\[([a-z_]+)\] ")


def make_finding(draft_id: Optional[str], code: str, explanation: str, supporting=None,
                 requested_context=(), requested_correction: Optional[str] = None,
                 *, run_id: Optional[str] = None) -> ReviewFinding:
    """Draft-level finding, or run-level when ``draft_id`` is None (then ``run_id`` is required).

    ``requested_context`` is a sequence of section/passage IDs that revise_extraction resolves;
    prose belongs in ``explanation``.
    """
    category, severity = CODE_MAP[code]
    kwargs = dict(severity=severity, supporting_passage_ids=tuple(supporting or ()),
                  requested_context=tuple(dict.fromkeys(i for i in (requested_context or ()) if i)),
                  requested_correction=requested_correction)
    text = f"[{code}] {explanation}"
    if draft_id is None:
        if not run_id:
            raise ValueError("a finding without a draft needs run_id")
        return ReviewFinding.for_run(run_id, category, text, **kwargs)
    return ReviewFinding.for_draft(draft_id, category, text, **kwargs)


def finding_code(f: ReviewFinding) -> Optional[str]:
    m = _CODE_RE.match(f.explanation or "")
    return m.group(1) if m else None


def has_request(f: ReviewFinding) -> bool:
    return bool(f.requested_context or f.requested_correction)


def make_report(run_id: str, accepted, findings, duplicate_mapping, rejected, remaining,
                *, partial: bool, usage: Optional[ModelUsage] = None) -> QualityReport:
    return QualityReport(
        run_id=run_id,
        accepted_draft_ids=tuple(accepted),
        findings=tuple(findings),
        duplicate_mapping=dict(duplicate_mapping),
        rejected=dict(rejected),
        remaining_issues=tuple(remaining),
        partial=partial,
        usage=usage or ModelUsage(),
    )


def make_validation_error(path: str, message: str, code: Optional[str] = None) -> ValidationError:
    return ValidationError(path=path, message=message, code=code)


def make_validation_report(valid, errors, official_ran, official_output, schema_identity=None,
                           index_identity=None, checked_at=None) -> ValidationReport:
    return ValidationReport(
        valid=valid,
        errors=tuple(errors),
        official_validator_ran=official_ran,
        official_validator_output=official_output,
        schema_identity=schema_identity,
        index_identity=index_identity,
        checked_at=checked_at,
    )


def emit(emit_event: Any, stage: str, message: str, completed=None, total=None) -> None:
    """Send one RunEvent. A failing callback never fails the review."""
    if emit_event is None:
        return
    try:
        emit_event(make_event(stage, message, completed=completed, total=total))
    except Exception:  # noqa: BLE001
        pass


def usage_of(resp: Any) -> ModelUsage:
    """Measured usage of one response; a bare call count when the adapter reports none."""
    u = getattr(resp, "usage", None)
    return u if isinstance(u, ModelUsage) and u.calls else ModelUsage(calls=1)
