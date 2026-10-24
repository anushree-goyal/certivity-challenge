"""Deterministic checks run over every draft (no model calls).

Outcomes per draft:
  rejected        - cannot be exported as written (missing/wrong-version primary source)
  needs_revision  - structurally incomplete; goes back via findings, never auto-fixed
  flagged         - exportable so far, but a heuristic wants semantic review
  accepted        - passed every deterministic check (NOT the same as semantically verified)
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from . import _access as A
from ._records import make_finding
from .limits import MAX_TEXT_CHARS

# Finding categories
SOURCE_MISSING = "source_missing"
WRONG_VERSION = "wrong_version_or_document"
SNAPSHOT_MISMATCH = "snapshot_mismatch"
PAGE_UNVERIFIED = "page_unverified"
NO_PRIMARY = "no_primary_passage"
PRIMARY_NOT_SUPPORTING = "primary_not_in_supporting"
EMPTY_TEXT = "empty_text"
TEXT_TOO_LONG = "text_too_long"
DUPLICATE_DRAFT_ID = "duplicate_draft_id"
NUMERIC_NOT_IN_EVIDENCE = "numeric_not_in_evidence"
POSSIBLE_LOST_CONDITION = "possible_lost_condition"
PRIMARY_SPANS_PAGES = "primary_spans_pages"

REJECT = {SOURCE_MISSING, WRONG_VERSION, SNAPSHOT_MISMATCH}
NEEDS_REVISION = {PAGE_UNVERIFIED, NO_PRIMARY, PRIMARY_NOT_SUPPORTING, EMPTY_TEXT,
                  TEXT_TOO_LONG, DUPLICATE_DRAFT_ID}
# everything else is a non-blocking flag for semantic review

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_EXCEPTION_MARKERS = re.compile(
    r"\b(unless|except|provided that|only if|only when|does not apply|shall not apply|"
    r"with the exception)\b", re.I)


def verified_page(value) -> int | None:
    """A usable printed page is a positive integer (or digit string)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
        return n if n > 0 else None
    return None


def _numbers(text: str) -> set[str]:
    return {m.replace(",", ".") for m in _NUM.findall(text)}


@dataclass
class DeterministicResult:
    accepted_ids: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    needs_revision: dict[str, str] = field(default_factory=dict)
    flagged_ids: list[str] = field(default_factory=list)
    findings: list = field(default_factory=list)


def check_drafts(run_input, pipeline_draft, corpus) -> DeterministicResult:
    result = DeterministicResult()
    drafts = A.drafts_of(pipeline_draft)
    base_docs = {A.doc_id(d): d for d in corpus.list_documents(role="base")}
    snapshot = A.run_snapshot_id(run_input)

    id_counts = Counter(A.draft_id(d) for d in drafts)
    wanted: list[str] = []
    for d in drafts:
        wanted += A.draft_supporting_ids(d)
        if A.draft_primary_id(d):
            wanted.append(A.draft_primary_id(d))
    passages = {A.passage_id(p): p for p in corpus.get_passages(sorted(set(wanted)))}

    for d in drafts:
        did = A.draft_id(d)
        local: list = []

        def add(category, explanation, supporting=None, requested=None):
            local.append(make_finding(did, category, explanation, supporting, requested))

        if not did or id_counts[did] > 1:
            add(DUPLICATE_DRAFT_ID, f"draft_id {did!r} is missing or used more than once; "
                "the drafts cannot be told apart.")
            result.findings += local
            if did:
                result.needs_revision.setdefault(did, DUPLICATE_DRAFT_ID)
            continue

        text = A.draft_text(d)
        if not text.strip():
            add(EMPTY_TEXT, "Draft has no obligation text.")
        elif len(text) > MAX_TEXT_CHARS:
            add(TEXT_TOO_LONG,
                f"Text is {len(text)} characters (limit {MAX_TEXT_CHARS}). It must not be "
                "truncated; request a faithful reformulation or a split only if it contains "
                "separate obligations.",
                requested="faithful reformulation/split")

        primary_id = A.draft_primary_id(d)
        support_ids = A.draft_supporting_ids(d)
        primary = passages.get(primary_id) if primary_id else None

        if not primary_id:
            add(NO_PRIMARY, "Draft names no primary supporting passage.",
                requested="primary passage that supports the stated obligation")
        elif primary is None:
            add(SOURCE_MISSING, f"Primary passage {primary_id!r} does not exist in snapshot "
                f"{snapshot!r}.", [primary_id])
        elif support_ids and primary_id not in support_ids:
            add(PRIMARY_NOT_SUPPORTING, "Primary passage is not among the supporting passages.",
                [primary_id])

        evidence = []
        for pid in dict.fromkeys(([primary_id] if primary_id else []) + support_ids):
            p = passages.get(pid)
            if p is None:
                if pid != primary_id:
                    add(SOURCE_MISSING, f"Supporting passage {pid!r} not found in the snapshot.",
                        [pid], requested="valid supporting passage or removal")
                continue
            evidence.append(p)
            doc = base_docs.get(A.passage_document_id(p))
            if doc is None:
                cat = WRONG_VERSION
                add(cat, f"Passage {pid!r} belongs to document {A.passage_document_id(p)!r}, "
                    "which is not a base-version document of this snapshot (latest or unknown).",
                    [pid])
            elif snapshot and A.doc_snapshot(doc) and A.doc_snapshot(doc) != snapshot:
                add(SNAPSHOT_MISMATCH, f"Document snapshot {A.doc_snapshot(doc)!r} differs from "
                    f"the run snapshot {snapshot!r}.", [pid])

        if primary is not None:
            if verified_page(A.passage_printed_page(primary)) is None:
                add(PAGE_UNVERIFIED, "Primary passage has no verified positive printed page.",
                    [primary_id], requested="verified printed-page mapping")
            if A.passage_spans_pages(primary):
                add(PRIMARY_SPANS_PAGES, "Primary passage spans several pages; the cited start "
                    "page may not be where the stated obligation appears.", [primary_id])

        # Heuristic flags for semantic review (never reasons to reject).
        ev_text = " ".join(A.passage_text(p) for p in evidence)
        if evidence and text.strip():
            missing = sorted(_numbers(text) - _numbers(ev_text))
            if missing:
                add(NUMERIC_NOT_IN_EVIDENCE,
                    f"Numbers {missing} in the draft do not appear in the cited evidence "
                    "(may be a wording/unit difference or an unsupported value).",
                    [primary_id] if primary_id else [], requested="check value/unit against source")
            if (_EXCEPTION_MARKERS.search(ev_text) and not _EXCEPTION_MARKERS.search(text)
                    and not A.draft_condition(d)):
                add(POSSIBLE_LOST_CONDITION,
                    "Evidence contains exception/condition wording that the draft text and "
                    "condition field do not carry (heuristic).",
                    [primary_id] if primary_id else [], requested="confirm no condition was lost")

        result.findings += local
        cats = {f.category for f in local}
        if cats & REJECT:
            c = sorted(cats & REJECT)[0]
            result.rejected[did] = f"{c}: " + next(f.explanation for f in local if f.category == c)
        elif cats & NEEDS_REVISION:
            c = sorted(cats & NEEDS_REVISION)[0]
            result.needs_revision[did] = c
        else:
            result.accepted_ids.append(did)
            if cats:
                result.flagged_ids.append(did)
    return result
