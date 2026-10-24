"""The systematic inspection plan (owner: Anushree).

Coverage comes from walking the selected documents in reading order, not from
retrieval top-k. Search helps resolve a reference; it does not decide what gets
read. Every section a document has lands in this plan with a status, so an
unprocessed section is visible rather than absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from certivity.contracts import (
    CoverageItem,
    CoverageStatus,
    Document,
    Section,
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
    ctx: RunContext, documents: Iterable[Document]
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

    ctx.event(
        "plan",
        f"Inspection plan: {len(plan)} section(s) across "
        f"{len({item.document.document_id for item in plan})} document(s)",
        total=len(plan),
    )
    return plan, coverage


def _order_key(section: Section) -> tuple[int, int, str]:
    """Reading order: ordinal when the corpus supplies one, else namespace/number."""
    return (section.ordinal, section.depth, section.paragraph_label or section.heading or "")


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
