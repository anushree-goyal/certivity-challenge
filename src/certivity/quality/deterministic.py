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
from decimal import Decimal, InvalidOperation

from . import _access as A
from ._records import finding_code, make_finding
from .limits import MAX_TEXT_CHARS

# Finding categories
SOURCE_MISSING = "source_missing"
SUPPORTING_INVALID = "supporting_passage_invalid"
WRONG_VERSION = "wrong_version_or_document"
SNAPSHOT_MISMATCH = "snapshot_mismatch"
PAGE_UNVERIFIED = "page_unverified"
NO_PRIMARY = "no_primary_passage"
EMPTY_TEXT = "empty_text"
TEXT_TOO_LONG = "text_too_long"
DUPLICATE_DRAFT_ID = "duplicate_draft_id"
NUMERIC_NOT_IN_EVIDENCE = "numeric_not_in_evidence"
POSSIBLE_LOST_CONDITION = "possible_lost_condition"
PRIMARY_SPANS_PAGES = "primary_spans_pages"
PRIMARY_IS_CONTEXT = "primary_is_context_passage"
UNRESOLVED_REFERENCE = "unresolved_reference"

# Set by extraction when the primary citation is a context/referenced passage (contracts 1.5.0 notes).
_CONTEXT_PRIMARY_NOTE = "primary citation is a context or referenced passage"

REJECT = {SOURCE_MISSING, WRONG_VERSION, SNAPSHOT_MISMATCH}
NEEDS_REVISION = {SUPPORTING_INVALID, PAGE_UNVERIFIED, NO_PRIMARY, EMPTY_TEXT, TEXT_TOO_LONG,
                  DUPLICATE_DRAFT_ID}
# everything else is a non-blocking flag for semantic review

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
# The corpus writes exponents with "^": 4∙10^0 (= 4), 100 cm^2 (= 100 cm²), and a footnote
# marker as ^1 directly after text ("lamps^1 shall").
_SCIENTIFIC = re.compile(r"(\d+(?:[.,]\d+)?)\s*[∙·⋅×*]\s*10\s*\^\s*([-−]?\d+)")
_UNIT_POWER = re.compile(r"(?<![A-Za-z])(mm|cm|dm|km|m|s)\^([-−]?\d)(?!\d)")
_FOOTNOTE = re.compile(r"(?<=[^\d\s^])\^\d+")
_SUPERSCRIPT = str.maketrans("-−0123456789", "⁻⁻⁰¹²³⁴⁵⁶⁷⁸⁹")
_EXCEPTION_MARKERS = re.compile(
    r"\b(unless|except|provided that|only if|only when|does not apply|shall not apply|"
    r"with the exception)\b", re.I)


def verified_page(value) -> int | None:
    """A usable printed page is a positive integer or digit-only label ("12"). Labels such as
    "ii" or "A-3" cannot be the integer source.page the official schema requires."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
        return n if n > 0 else None
    return None


def _scientific(m: re.Match) -> str:
    try:
        value = Decimal(m.group(1).replace(",", ".")).scaleb(int(m.group(2).replace("−", "-")))
    except InvalidOperation:
        return m.group(0)
    return format(value.normalize(), "f")


def normalise_numbers(text: str) -> str:
    """Corpus exponent notation to plain values, so 42 and 4.2∙10^1 compare equal."""
    text = _SCIENTIFIC.sub(_scientific, text)
    text = _UNIT_POWER.sub(lambda m: m.group(1) + m.group(2).translate(_SUPERSCRIPT), text)
    return _FOOTNOTE.sub("", text)


def _numbers(text: str) -> set[str]:
    return {m.replace(",", ".") for m in _NUM.findall(normalise_numbers(text))}


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
    passages = {A.passage_id(p): p for p in A.get_passages(corpus, sorted(set(wanted)))}

    for d in drafts:
        did = A.draft_id(d)
        local: list = []

        def add(code, explanation, supporting=None, context=(), correction=None):
            local.append(make_finding(did, code, explanation, supporting, context, correction))

        if not did or id_counts[did] > 1:
            add(DUPLICATE_DRAFT_ID, f"draft_id {did!r} is missing or used more than once; "
                "the drafts cannot be told apart.",
                correction="give every draft a unique, non-empty draft_id")
            result.findings += local
            if did:
                result.needs_revision.setdefault(did, DUPLICATE_DRAFT_ID)
            continue

        text = A.draft_text(d)
        if not text.strip():
            add(EMPTY_TEXT, "Draft has no obligation text.",
                correction="provide the obligation text or drop the draft")
        elif len(text) > MAX_TEXT_CHARS:
            add(TEXT_TOO_LONG,
                f"Text is {len(text)} characters (limit {MAX_TEXT_CHARS}). It must not be "
                "truncated; request a faithful reformulation or a split only if it contains "
                "separate obligations.",
                correction="faithful reformulation within the limit, or a split only if the text contains separate obligations")

        primary_id = A.draft_primary_id(d)
        support_ids = A.draft_supporting_ids(d)
        primary = passages.get(primary_id) if primary_id else None

        if not primary_id:
            add(NO_PRIMARY, "Draft names no primary supporting passage.",
                correction="name the one primary passage that supports the stated obligation")
        elif primary is None:
            add(SOURCE_MISSING, f"Primary passage {primary_id!r} does not exist in snapshot "
                f"{snapshot!r}.", [primary_id])

        evidence = []
        for pid in dict.fromkeys(([primary_id] if primary_id else []) + support_ids):
            p = passages.get(pid)
            is_primary = pid == primary_id
            if p is None:
                if not is_primary:
                    add(SUPPORTING_INVALID, f"Supporting passage {pid!r} not found in the snapshot.",
                        [pid], correction="replace with a valid supporting passage or remove it")
                continue
            doc = base_docs.get(A.passage_document_id(p))
            problem = None
            if doc is None:
                problem = (WRONG_VERSION, f"Passage {pid!r} belongs to document "
                           f"{A.passage_document_id(p)!r}, which is not a base-version document of "
                           "this snapshot (latest or unknown).")
            elif snapshot and A.doc_snapshot(doc) and A.doc_snapshot(doc) != snapshot:
                problem = (SNAPSHOT_MISMATCH, f"Document snapshot {A.doc_snapshot(doc)!r} differs "
                           f"from the run snapshot {snapshot!r}.")
            if problem and not is_primary:
                # Only the supporting reference is bad: ask for it to be fixed, don't reject the draft.
                add(SUPPORTING_INVALID, problem[1], [pid],
                    correction="replace with a base-version supporting passage or remove it")
                continue
            if problem:
                add(problem[0], problem[1], [pid])
            evidence.append(p)

        if primary is not None:
            if verified_page(A.passage_printed_page(primary)) is None:
                add(PAGE_UNVERIFIED, f"Primary passage has no verified positive printed page "
                    f"(label {A.passage_printed_page(primary)!r}); the official source.page cannot "
                    "be filled without guessing.", [primary_id],
                    correction="cite a primary passage whose printed page is verified; if this "
                    "passage is the right one, its page mapping needs fixing in the corpus")
            if A.passage_spans_pages(primary):
                add(PRIMARY_SPANS_PAGES, "Primary passage spans several pages; the cited start "
                    "page may not be where the stated obligation appears.", [primary_id])
        if _CONTEXT_PRIMARY_NOTE in A.draft_notes(d).casefold():
            add(PRIMARY_IS_CONTEXT, "Extraction cited a context or referenced passage as primary; "
                "it may not support the obligation on its own.", [primary_id] if primary_id else [])

        for ref in A.draft_unresolved_references(d):
            add(UNRESOLVED_REFERENCE, f"Reference {ref.request.raw_reference!r} is "
                f"{ref.status.value}: {ref.explanation} The obligation may depend on text that was "
                "not read (possible lost reference).", list(ref.candidate_passage_ids))

        # Heuristic flags for semantic review (never reasons to reject).
        ev_text = " ".join(A.passage_text(p) for p in evidence)
        if evidence and text.strip():
            missing = sorted(_numbers(text) - _numbers(ev_text))
            if missing:
                add(NUMERIC_NOT_IN_EVIDENCE,
                    f"Numbers {missing} in the draft do not appear in the cited evidence "
                    "(may be a wording/unit difference or an unsupported value).",
                    [primary_id] if primary_id else [])
            if (_EXCEPTION_MARKERS.search(ev_text) and not _EXCEPTION_MARKERS.search(text)
                    and not A.draft_condition(d) and not A.draft_exceptions(d)):
                add(POSSIBLE_LOST_CONDITION,
                    "Evidence contains exception/condition wording that the draft text and "
                    "condition field do not carry (heuristic).",
                    [primary_id] if primary_id else [])

        result.findings += local
        cats = {finding_code(f) for f in local}
        if cats & REJECT:
            c = sorted(cats & REJECT)[0]
            result.rejected[did] = next(f.explanation for f in local if finding_code(f) == c)
        elif cats & NEEDS_REVISION:
            c = sorted(cats & NEEDS_REVISION)[0]
            result.needs_revision[did] = c
        else:
            result.accepted_ids.append(did)
            if cats:
                result.flagged_ids.append(did)
    return result
