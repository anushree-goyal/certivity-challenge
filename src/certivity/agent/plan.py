"""The systematic inspection plan (owner: Anushree).

Coverage comes from walking the selected documents in reading order, not from
retrieval top-k. Search helps resolve a reference; it does not decide what gets
read. Every section a document has lands in this plan with a status, so an
unprocessed section is visible rather than absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from certivity.contracts import (
    CoverageItem,
    CoverageStatus,
    Document,
    ProductProfile,
    Section,
    SourcePassage,
    TargetKind,
)
from certivity.agent.context import RunContext

_NON_SUBSTANTIVE_HEADINGS = (
    "table of contents",
    "contents",
    "list of annexes",
    "index",
)
"""Headings excluded as navigation apparatus. The list is deliberately tiny:
anything arguable is inspected and recorded, because an unread section cannot
be distinguished later from a section with nothing in it.

Definitions, scope, markings, installation, tests, tables and annexes are all
substantive and are never excluded here - a definition can carry an operative
measurement rule."""


# --------------------------------------------------------------------------- #
# Inspection priority
#
# Coverage is still every section; priority only decides the ORDER they are
# read in. That matters because a run can be stopped by a model budget, a time
# budget or a cancellation, and whatever was read by then is what the
# submission contains.
#
# The first real end-to-end run made the case: it spent its whole budget on
# UN R10's definitions clause - ESA types, immunity-related functions - and
# produced four requirements that appear nowhere in the dev case's answer key,
# while R148's photometric and marking sections went unread.
# --------------------------------------------------------------------------- #

_OBLIGATION_WORDS = ("shall", "must", "shall not", "may not")
_PERMISSION_WORDS = ("may", "is permitted", "need not")

_LOW_VALUE_HEADINGS = (
    "transitional provisions",
    "names and addresses",
    "modification of the type",
    "conformity of production",
    "penalties for non-conformity",
    "production definitively discontinued",
    "communication",
    "general remarks",
)
"""Administrative apparatus that binds the approval authority rather than the
product. Deprioritised, never excluded - a submission can legitimately cite
them, they are just the last thing worth spending a bounded budget on."""


def _profile_terms(profile: Optional[ProductProfile]) -> set[str]:
    """Lowercased words describing the product, for overlap scoring."""
    if profile is None:
        return set()
    parts: list[str] = [profile.product_type or "", profile.summary or ""]
    parts.extend(profile.vehicle_categories)
    for group in (profile.facts, profile.components, profile.functions, profile.materials):
        for fact in group:
            parts.append(fact.key.replace("_", " "))
            parts.append(fact.value or "")
    words = {
        w
        for part in parts
        for w in re.split(r"[^a-z0-9]+", part.lower())
        if len(w) > 3
    }
    return words - {"lamp", "lamps", "vehicle", "vehicles", "device", "devices"}


def section_priority(
    section: Section, passages: list[SourcePassage], terms: set[str]
) -> float:
    """Higher means read sooner. Free to compute - no model call.

    Three signals, all cheap:

    * obligation language, because a section with no "shall" rarely states a
      requirement;
    * overlap with the product's own vocabulary, which is what distinguishes
      the rear-lamp sections of a regulation from its front-lamp sections;
    * a small penalty for administrative apparatus.

    Deliberately not a model call: ranking 856 sections with the model would
    cost more than reading the good ones.
    """
    text = " ".join(p.text for p in passages).lower()
    if not text.strip():
        # No readable text: nothing to extract, so this must sort last - below
        # even administrative apparatus, which can at least carry a citable
        # requirement. Reachable when a corpus read fails, since sections with
        # no passages never enter the plan in the first place.
        return -1000.0

    score = 0.0
    obligations = sum(text.count(w) for w in _OBLIGATION_WORDS)
    score += min(obligations, 8) * 1.5
    score += min(sum(text.count(w) for w in _PERMISSION_WORDS), 4) * 0.5

    if terms:
        hits = sum(1 for t in terms if t in text)
        score += min(hits, 10) * 2.0

    # A numeric limit is a strong sign of a checkable requirement.
    if re.search(r"\d\s*(cd|lx|mm|cm|%|°|v\b|k\b)", text):
        score += 3.0

    heading = (section.heading or "").strip().lower()
    if any(marker in heading for marker in _LOW_VALUE_HEADINGS):
        score -= 6.0
    if section.namespace == "main" and (section.paragraph_label or "").startswith("1"):
        score -= 1.0  # scope, read already for applicability

    return score


@dataclass(frozen=True)
class PlanItem:
    """One unit of work: a section, with the document it belongs to."""

    document: Document
    section: Section

    @property
    def section_id(self) -> str:
        return self.section.section_id


def _is_navigation_only(section: Section) -> bool:
    heading = (section.heading or "").strip().lower()
    return any(heading == marker for marker in _NON_SUBSTANTIVE_HEADINGS)


def build_inspection_plan(
    ctx: RunContext,
    documents: Iterable[Document],
    profile: Optional[ProductProfile] = None,
) -> tuple[list[PlanItem], list[CoverageItem]]:
    """Return the ordered work list and the initial coverage ledger.

    The ledger starts with one entry per document and one per section. Section
    entries start ``PENDING``; extraction flips them to ``PROCESSED`` or
    ``FAILED``, and this function marks the exclusions it makes here, with
    reasons.
    """
    plan: list[PlanItem] = []
    coverage: list[CoverageItem] = []

    for document in documents:
        sections = sorted(ctx.corpus.list_sections(document.document_id), key=_order_key)
        coverage.append(
            CoverageItem(
                target_kind=TargetKind.DOCUMENT,
                target_id=document.document_id,
                status=CoverageStatus.PENDING,
                explanation=f"{len(sections)} section(s) listed for {document.regulation}",
                document_id=document.document_id,
            )
        )
        if not sections:
            ctx.warn(
                f"{document.regulation} ({document.document_id}) has no sections in the "
                "corpus snapshot; nothing can be extracted from it",
                stage="plan",
            )
            continue

        kept = 0
        for section in sections:
            if _is_navigation_only(section):
                coverage.append(
                    CoverageItem(
                        target_kind=TargetKind.SECTION,
                        target_id=section.section_id,
                        status=CoverageStatus.EXCLUDED,
                        explanation=(
                            f"Navigation apparatus, not substantive text: "
                            f"{section.heading!r}"
                        ),
                        document_id=document.document_id,
                    )
                )
                continue
            if not section.primary_passage_ids:
                coverage.append(
                    CoverageItem(
                        target_kind=TargetKind.SECTION,
                        target_id=section.section_id,
                        status=CoverageStatus.EXCLUDED,
                        explanation=(
                            "Heading with no primary passages in the snapshot; its "
                            "children are inspected separately."
                        ),
                        document_id=document.document_id,
                    )
                )
                continue
            plan.append(PlanItem(document=document, section=section))
            coverage.append(
                CoverageItem(
                    target_kind=TargetKind.SECTION,
                    target_id=section.section_id,
                    status=CoverageStatus.PENDING,
                    explanation=None,
                    document_id=document.document_id,
                )
            )
            kept += 1

        limit = ctx.config.max_sections_per_document
        if limit is not None and kept > limit:
            plan, coverage = _apply_section_limit(
                ctx, plan, coverage, document, limit
            )

    plan = _prioritise(ctx, plan, profile)
    total_limit = getattr(ctx.config, "max_sections_total", None)
    if total_limit is not None and len(plan) > total_limit:
        plan, coverage = _apply_total_limit(ctx, plan, coverage, int(total_limit))
    ctx.event(
        "plan",
        f"Inspection plan: {len(plan)} section(s) across "
        f"{len({item.document.document_id for item in plan})} document(s), "
        "ordered by expected requirement density",
        total=len(plan),
    )
    return plan, coverage


def _prioritise(
    ctx: RunContext, plan: list[PlanItem], profile: Optional[ProductProfile]
) -> list[PlanItem]:
    """Reorder the plan so the most promising sections are read first.

    This changes order only. Every section stays in the plan and in the
    coverage ledger, so with an unbounded budget the result is identical; with
    a bounded one, the budget is spent on the sections most likely to state a
    requirement about this product.
    """
    terms = _profile_terms(profile)
    scored: list[tuple[float, int, PlanItem]] = []
    for index, item in enumerate(plan):
        try:
            passages = ctx.corpus.get_passages(list(item.section.primary_passage_ids))
        except Exception:
            passages = []
        scored.append((section_priority(item.section, passages, terms), index, item))

    # Descending score, document order as the tiebreak so reading stays coherent.
    scored.sort(key=lambda row: (-row[0], row[1]))
    ordered = [row[2] for row in scored]

    if ordered:
        top = ordered[:3]
        ctx.event(
            "plan",
            "First sections to read: "
            + "; ".join(
                f"{i.document.regulation} {i.section.paragraph_label or i.section.heading[:28]}"
                for i in top
            ),
        )
    return ordered


_LABEL_PART = re.compile(r"(\d+)|([^\d]+)")


def _natural_label_key(label: str) -> tuple:
    """Sort key that orders 3.2 before 3.10.

    A plain string sort puts "3.10" before "3.2", which silently reorders a
    whole document. Splitting into numeric and non-numeric runs and comparing
    numbers as numbers fixes it. Needed because the corpus does not yet supply
    ``Section.ordinal``, and a wrong reading order makes coverage misleading
    even when every section is eventually processed.
    """
    parts: list[tuple[int, int | str]] = []
    for number, text in _LABEL_PART.findall(label or ""):
        if number:
            parts.append((0, int(number)))
        else:
            parts.append((1, text.strip().lower()))
    return tuple(parts)


def _order_key(section: Section) -> tuple:
    """Reading order: the corpus's ordinal when it supplies one, else the label.

    ``ordinal`` is authoritative because only the corpus knows the physical
    order. When every section reports 0 - as happens when a corpus has not
    populated it - fall back to a natural sort of the paragraph label, grouped
    by namespace so annexes stay after the main body.
    """
    return (
        section.ordinal,
        section.depth,
        section.namespace or "",
        _natural_label_key(section.paragraph_label or section.heading or ""),
    )


def _apply_section_limit(
    ctx: RunContext,
    plan: list[PlanItem],
    coverage: list[CoverageItem],
    document: Document,
    limit: int,
) -> tuple[list[PlanItem], list[CoverageItem]]:
    """Trim one document's work to the configured cap, recording what was dropped.

    The dropped sections stay in the ledger as ``PENDING`` rather than vanishing,
    because the run's output must state that they were never read.
    """
    document_items = [item for item in plan if item.document.document_id == document.document_id]
    if len(document_items) <= limit:
        return plan, coverage
    dropped = {item.section_id for item in document_items[limit:]}
    ctx.warn(
        f"RunConfig.max_sections_per_document={limit} left {len(dropped)} section(s) of "
        f"{document.regulation} unread; coverage is partial",
        stage="plan",
    )
    kept_plan = [
        item
        for item in plan
        if item.document.document_id != document.document_id or item.section_id not in dropped
    ]
    updated = [
        CoverageItem(
            target_kind=entry.target_kind,
            target_id=entry.target_id,
            status=entry.status,
            explanation=(
                "Not read: RunConfig.max_sections_per_document was reached."
                if entry.target_id in dropped
                else entry.explanation
            ),
            inspected_passage_ids=entry.inspected_passage_ids,
            draft_ids=entry.draft_ids,
            document_id=entry.document_id,
        )
        for entry in coverage
    ]
    return kept_plan, updated


def _apply_total_limit(
    ctx: RunContext,
    plan: list[PlanItem],
    coverage: list[CoverageItem],
    limit: int,
) -> tuple[list[PlanItem], list[CoverageItem]]:
    """Keep the ``limit`` highest-priority sections, recording the rest as unread.

    Applied after prioritisation, so the sections kept are the ones most likely
    to carry a requirement rather than whichever happened to come first in the
    documents. The dropped ones stay in the ledger: a bounded run must say what
    it did not read, or its coverage claim is not honest.
    """
    kept, dropped_items = plan[:limit], plan[limit:]
    dropped = {item.section_id for item in dropped_items}
    by_document: dict[str, int] = {}
    for item in dropped_items:
        by_document[item.document.regulation] = (
            by_document.get(item.document.regulation, 0) + 1
        )
    detail = ", ".join(f"{reg}: {n}" for reg, n in sorted(by_document.items()))
    ctx.warn(
        f"RunConfig.max_sections_total={limit} left {len(dropped)} lower-priority "
        f"section(s) unread ({detail}); coverage is partial",
        stage="plan",
    )
    updated = [
        CoverageItem(
            target_kind=entry.target_kind,
            target_id=entry.target_id,
            status=entry.status,
            explanation=(
                "Not read: RunConfig.max_sections_total was reached; this section "
                "ranked below the cap on expected requirement density."
                if entry.target_id in dropped
                else entry.explanation
            ),
            inspected_passage_ids=entry.inspected_passage_ids,
            draft_ids=entry.draft_ids,
            document_id=entry.document_id,
        )
        for entry in coverage
    ]
    return kept, updated
