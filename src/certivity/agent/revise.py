"""Targeted revision from review findings (owner: Anushree).

Teammate 3 returns findings; the substantive correction happens here, in the
component that owns extraction semantics. Review rules stay in their component
and extraction rules stay in this one.

Only drafts a finding names are touched. Re-extracting the whole section would
throw away correct work and would make the effect of a finding impossible to
audit.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Sequence

from certivity.contracts import (
    DraftRequirement,
    ModelBudgetExceeded,
    PipelineDraft,
    ReviewFinding,
    SourcePassage,
    TargetKind,
)
from certivity.agent.context import RunContext
from certivity.agent.extract import _build_drafts, draft_id_for
from certivity.agent.profile import profile_payload
from certivity.agent.prompts import (
    PROMPT_VERSIONS,
    REVISE_OBLIGATIONS_INSTRUCTIONS,
    REVISE_OBLIGATIONS_SCHEMA,
)


def _draft_payload(draft: DraftRequirement) -> dict[str, Any]:
    return {
        "draft_id": draft.draft_id,
        "text": draft.text,
        "primary_passage_id": draft.primary_passage_id,
        "supporting_passage_ids": list(draft.supporting_passage_ids),
        "obligation_kind": draft.obligation_kind.value,
        "applicability_condition": draft.applicability_condition,
        "applicability_reason": draft.applicability_reason,
        "exceptions": list(draft.exceptions),
        "values": [
            {
                "quantity": v.quantity,
                "value": v.value,
                "unit": v.unit,
                "comparator": v.comparator,
            }
            for v in draft.values
        ],
    }


def _passage_payload(passages: Iterable[SourcePassage]) -> list[dict[str, Any]]:
    return [
        {
            "passage_id": p.passage_id,
            "namespace": p.namespace,
            "paragraph_label": p.paragraph_label,
            "printed_page": p.printed_start_page,
            "is_table": p.is_table,
            "table_context": p.table_context,
            "text": p.text,
        }
        for p in passages
    ]


def _gather_passages(
    ctx: RunContext,
    drafts: Sequence[DraftRequirement],
    findings: Sequence[ReviewFinding],
) -> list[SourcePassage]:
    """Every passage the drafts cite, plus whatever context the reviewer asked for."""
    wanted: list[str] = []
    for draft in drafts:
        wanted.extend(draft.supporting_passage_ids)
    for finding in findings:
        wanted.extend(finding.supporting_passage_ids)
        wanted.extend(pid for pid in finding.requested_context if "@" in pid)

    passages = list(ctx.corpus.get_passages(list(dict.fromkeys(wanted))))

    # A requested context entry that is a section id rather than a passage id is
    # resolved through the section, which is how a reviewer asks for the
    # surrounding conditions they think were missed.
    section_requests = {
        pid
        for finding in findings
        for pid in finding.requested_context
        if "@" not in pid and "#" in pid
    }
    known = {p.passage_id for p in passages}
    for section_id in section_requests:
        try:
            bundle = ctx.corpus.read_section(section_id, include_context=True)
        except Exception as exc:
            ctx.warn(
                f"review requested context from section {section_id}, which could not be "
                f"read: {exc}",
                stage="revise",
            )
            continue
        for passage in (*bundle.primary_passages, *bundle.context_passages):
            if passage.passage_id not in known:
                passages.append(passage)
                known.add(passage.passage_id)
    return passages


def revise_drafts(
    ctx: RunContext,
    draft: PipelineDraft,
    findings: Sequence[ReviewFinding],
) -> tuple[list[DraftRequirement], list[str]]:
    """Return the full replacement draft list plus notes on what happened.

    Drafts no finding names are carried through unchanged.
    """
    by_draft: dict[str, list[ReviewFinding]] = defaultdict(list)
    for finding in findings:
        if finding.target_kind is TargetKind.DRAFT:
            by_draft[finding.target_id].append(finding)

    unknown = set(by_draft) - {d.draft_id for d in draft.draft_requirements}
    for draft_id in sorted(unknown):
        ctx.warn(
            f"review finding addressed unknown draft {draft_id!r}; it was ignored",
            stage="revise",
        )
        by_draft.pop(draft_id, None)

    if not by_draft:
        return list(draft.draft_requirements), ["no draft-level findings to act on"]

    # Group by section so one call can see a section's drafts together, which is
    # what a "missing condition" finding usually needs.
    by_section: dict[str, list[DraftRequirement]] = defaultdict(list)
    for item in draft.draft_requirements:
        if item.draft_id in by_draft:
            by_section[item.section_id or "unknown"].append(item)

    profile_facts = profile_payload(draft.product_profile)
    replacements: dict[str, DraftRequirement | None] = {}
    notes: list[str] = []

    for position, (section_id, section_drafts) in enumerate(sorted(by_section.items()), 1):
        section_findings = [f for item in section_drafts for f in by_draft[item.draft_id]]
        passages = _gather_passages(ctx, section_drafts, section_findings)
        if not passages:
            ctx.warn(
                f"no source passages could be loaded for the drafts of {section_id}; "
                "their findings remain unresolved",
                stage="revise",
            )
            continue

        try:
            response = ctx.call_json(
                "revise_obligations",
                REVISE_OBLIGATIONS_INSTRUCTIONS,
                {
                    "product": profile_facts,
                    "section_id": section_id,
                    "drafts": [_draft_payload(item) for item in section_drafts],
                    "findings": [
                        {
                            "finding_id": f.finding_id,
                            "draft_id": f.target_id,
                            "category": f.category.value,
                            "severity": f.severity.value,
                            "explanation": f.explanation,
                            "requested_correction": f.requested_correction,
                        }
                        for f in section_findings
                    ],
                    "source_passages": _passage_payload(passages),
                },
                REVISE_OBLIGATIONS_SCHEMA,
                prompt_version=PROMPT_VERSIONS["revise_obligations"],
            )
        except ModelBudgetExceeded:
            ctx.warn(
                "model budget exhausted during revision; the remaining findings are "
                "unresolved and their drafts are unchanged",
                stage="revise",
            )
            break

        allowed = {p.passage_id for p in passages}
        _apply_decisions(
            ctx,
            response.data.get("decisions") or (),
            section_drafts=section_drafts,
            section_id=section_id,
            allowed_passage_ids=allowed,
            replacements=replacements,
            notes=notes,
        )
        ctx.event(
            "revise",
            f"Revised {section_id}",
            completed=position,
            total=len(by_section),
            affected_ids=[item.draft_id for item in section_drafts],
        )

    revised: list[DraftRequirement] = []
    for item in draft.draft_requirements:
        if item.draft_id not in replacements:
            revised.append(item)
            continue
        replacement = replacements[item.draft_id]
        if replacement is not None:
            revised.append(replacement)
    return revised, notes


def _apply_decisions(
    ctx: RunContext,
    decisions: Iterable[dict[str, Any]],
    *,
    section_drafts: Sequence[DraftRequirement],
    section_id: str,
    allowed_passage_ids: set[str],
    replacements: dict[str, DraftRequirement | None],
    notes: list[str],
) -> None:
    known = {item.draft_id: item for item in section_drafts}

    for decision in decisions:
        draft_id = str(decision.get("draft_id") or "")
        original = known.get(draft_id)
        if original is None:
            continue
        action = decision.get("action")
        explanation = str(decision.get("explanation") or "").strip()

        if action == "keep":
            notes.append(f"{draft_id}: kept unchanged - {explanation}")
            continue
        if action == "withdraw":
            replacements[draft_id] = None
            notes.append(f"{draft_id}: withdrawn - {explanation}")
            ctx.event("revise", f"Withdrew {draft_id}", affected_ids=[draft_id])
            continue
        if action == "unresolved":
            notes.append(f"{draft_id}: unresolved - {explanation}")
            ctx.warn(
                f"{draft_id}: revision could not be completed from the available "
                f"source - {explanation}",
                stage="revise",
            )
            continue

        obligation = decision.get("obligation")
        if not isinstance(obligation, dict):
            notes.append(
                f"{draft_id}: a revision was requested but no corrected obligation was "
                "returned; the original is unchanged"
            )
            continue

        rebuilt = _build_drafts(
            ctx,
            [obligation],
            section_id=original.section_id or section_id,
            document_id=original.document_id or "",
            allowed_passage_ids=allowed_passage_ids,
            primary_candidate_ids=allowed_passage_ids,
        )
        if not rebuilt:
            notes.append(
                f"{draft_id}: the corrected obligation was not citable against the "
                "supplied passages; the original is unchanged"
            )
            continue

        new = rebuilt[0]
        replacements[draft_id] = DraftRequirement(
            draft_id=draft_id_for(
                new.section_id or section_id, new.primary_passage_id, new.text
            ),
            text=new.text,
            supporting_passage_ids=new.supporting_passage_ids,
            primary_passage_id=new.primary_passage_id,
            obligation_kind=new.obligation_kind,
            applicability_condition=new.applicability_condition,
            applicability_reason=new.applicability_reason,
            exceptions=new.exceptions,
            values=new.values,
            unresolved_references=original.unresolved_references,
            document_id=original.document_id,
            section_id=original.section_id or section_id,
            prompt_version=PROMPT_VERSIONS["revise_obligations"],
            revision_of=draft_id,
            notes=new.notes,
        )
        notes.append(f"{draft_id}: revised - {explanation}")
