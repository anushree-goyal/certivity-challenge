"""Official submission construction from accepted drafts and trusted source records."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import _access as A
from .deterministic import verified_page
from ._records import set_export_mapping
from .limits import MAX_OUTPUT_BYTES, MAX_REQUIREMENTS, MAX_TEXT_CHARS


class SubmissionBuildError(Exception):
    """Raised when output would violate a hard limit. Nothing is truncated to avoid it."""


@dataclass
class BuildResult:
    submission: dict
    trace: list[dict] = field(default_factory=list)      # requirement id -> draft/evidence
    excluded: dict[str, str] = field(default_factory=dict)  # draft_id -> reason
    blocking_issues: list[str] = field(default_factory=list)
    size_bytes: int = 0
    export_mapping: dict[str, str] = field(default_factory=dict)  # draft ID -> requirement ID
    warnings: list[str] = field(default_factory=list)


def serialized_size(submission: dict) -> int:
    return len(json.dumps(submission, ensure_ascii=False).encode("utf-8"))


def build_submission_detailed(run_input, draft, report, corpus) -> BuildResult:
    drafts = {A.draft_id(d): d for d in A.drafts_of(draft)}
    base_docs = {A.doc_id(d): d for d in corpus.list_documents(role="base")}
    accepted = list(dict.fromkeys(_accepted_ids(report)))

    primaries = {}
    for d in (drafts.get(i) for i in accepted):
        if d is not None and A.draft_primary_id(d):
            primaries[A.draft_primary_id(d)] = None
    for p in corpus.get_passages(sorted(primaries)):
        primaries[A.passage_id(p)] = p

    result = BuildResult(submission={})
    requirements: list[dict] = []
    for did in accepted:
        d = drafts.get(did)
        if d is None:
            result.excluded[did] = "accepted ID has no matching draft"
            continue
        text = A.draft_text(d).strip()
        p = primaries.get(A.draft_primary_id(d))
        if not text:
            result.excluded[did] = "empty text"
        elif len(text) > MAX_TEXT_CHARS:
            result.excluded[did] = (f"text is {len(text)} chars > {MAX_TEXT_CHARS}; needs faithful "
                                    "reformulation/split, not truncation")
        elif p is None:
            result.excluded[did] = "primary passage not found"
        elif base_docs.get(A.passage_document_id(p)) is None:
            result.excluded[did] = "primary passage is not in a base-version document"
        elif verified_page(A.passage_printed_page(p)) is None:
            result.excluded[did] = "no verified positive printed page"
        else:
            doc = base_docs[A.passage_document_id(p)]
            source = {"regulation": A.doc_regulation(doc), "version": A.doc_version(doc),
                      "page": verified_page(A.passage_printed_page(p))}
            if A.passage_paragraph(p):
                source["paragraph"] = A.passage_paragraph(p)
            req_id = f"REQ-{len(requirements) + 1:04d}"
            requirements.append({"id": req_id, "text": text, "source": source})
            result.export_mapping[did] = req_id
            result.trace.append({"requirement_id": req_id, "draft_id": did,
                                 "primary_passage_id": A.draft_primary_id(d),
                                 "supporting_passage_ids": A.draft_supporting_ids(d)})

    result.submission = {"team_id": A.run_team_id(run_input), "case_id": A.run_case_id(run_input),
                         "requirements": requirements}
    result.size_bytes = serialized_size(result.submission)
    if len(requirements) > MAX_REQUIREMENTS:
        result.blocking_issues.append(
            f"{len(requirements)} requirements exceed the limit of {MAX_REQUIREMENTS}")
    if result.size_bytes > MAX_OUTPUT_BYTES:
        result.blocking_issues.append(
            f"output is {result.size_bytes} bytes, over the {MAX_OUTPUT_BYTES}-byte limit")
    if result.blocking_issues:
        result.export_mapping = {}  # nothing is exportable as-is
    if not set_export_mapping(report, result.export_mapping):
        result.warnings.append("QualityReport has no settable export_mapping; use "
                               "BuildResult.export_mapping instead")
    return result


def _accepted_ids(report) -> list[str]:
    return list(A.pick(report, "accepted_draft_ids", default=[]) or [])


def build_submission(run_input, draft, report, corpus) -> dict:
    """Official dict (team_id, case_id, requirements). Raises rather than truncating.

    Side effect: sets report.export_mapping (draft ID -> requirement ID) to what was actually
    exported, so the app can match drafts to requirements without relying on export order.

    Drafts that cannot be exported are omitted from the dict; use
    build_submission_detailed() to see which and why.
    """
    result = build_submission_detailed(run_input, draft, report, corpus)
    if result.blocking_issues:
        raise SubmissionBuildError("; ".join(result.blocking_issues))
    return result.submission
