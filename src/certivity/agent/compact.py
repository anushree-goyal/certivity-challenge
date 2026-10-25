"""Compact extraction: obligation paragraphs, many per call (owner: Anushree).

The section-by-section extraction sends a whole section with its context to
the model, gets back a long structured answer, and reads a section again for
each cross-reference: about 2.4 calls and over 10,000 tokens per section on
the real corpus. Most of that buys nothing: the answer key holds about one
requirement per paragraph, and the citation, page and values of a requirement
are facts the corpus already has.

Here the model sees only what needs judgement:

* the obligation paragraphs of the kept sections - chosen for free by
  ``economy.obligation_paragraphs`` - about 20 to a call, each with its id,
  label, heading path and text;
* and answers per paragraph: its requirements (one obligation each, plain
  English, with a condition), or "does not apply to this part", or "no
  obligation".

Citation, page, supporting passage and values are filled in by code from the
paragraph itself, so they cannot drift from the source. A regulation whose
paragraphs all come back "not applicable" yields no requirement and is not
cited - relevance is decided where the evidence is, paragraph by paragraph.

Anything the model does not answer (a failed batch, a missing paragraph) falls
back to the rule-based requirement for that paragraph, labelled as such.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from certivity.contracts import (
    DraftRequirement,
    LLMCallError,
    LLMConfigurationError,
    ModelBudgetExceeded,
    ObligationKind,
    ProductProfile,
    SourcePassage,
)
from certivity.agent.categories import PartCategory, compact_profile
from certivity.agent.context import RunContext
from certivity.agent.economy import (
    ECONOMY_NOTE,
    FALLBACK_MODES,
    MAX_TEXT_CHARS,
    economy_requirement,
    obligation_paragraphs,
    switch_to_economy,
    values_in,
)
from certivity.agent.extract import SectionResult, draft_id_for
from certivity.agent.plan import PlanItem
from certivity.agent.prompts import (
    COMPACT_OBLIGATIONS_INSTRUCTIONS,
    COMPACT_OBLIGATIONS_SCHEMA,
    PROMPT_VERSIONS,
)

PARAGRAPHS_PER_CALL = 20
CHARS_PER_CALL = 16_000
"""A call holds up to 20 paragraphs or about 16,000 characters of text,
whichever comes first: enough to amortise the instructions, small enough that
one answer stays short and one failure loses little."""
MAX_PARAGRAPH_CHARS = 2_500
MAX_REQUIREMENTS_PER_PARAGRAPH = 6

COMPACT_NOTE = "Interpreted paragraph by paragraph (compact extraction)."


def _payload_paragraph(item: PlanItem, passage: SourcePassage) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", passage.text).strip()
    return {
        "passage_id": passage.passage_id,
        "regulation": item.document.regulation,
        "label": passage.paragraph_label,
        "heading_path": " > ".join(passage.heading_ancestry[-3:]) or (item.section.heading or ""),
        "text": text[:MAX_PARAGRAPH_CHARS],
    }


def _batches(work: list[tuple[PlanItem, SourcePassage]]) -> list[list[tuple[PlanItem, SourcePassage]]]:
    batches: list[list[tuple[PlanItem, SourcePassage]]] = []
    current: list[tuple[PlanItem, SourcePassage]] = []
    size = 0
    for entry in work:
        n = min(len(entry[1].text), MAX_PARAGRAPH_CHARS)
        if current and (len(current) >= PARAGRAPHS_PER_CALL or size + n > CHARS_PER_CALL):
            batches.append(current)
            current, size = [], 0
        current.append(entry)
        size += n
    if current:
        batches.append(current)
    return batches


def _call(
    ctx: RunContext,
    batch: list[tuple[PlanItem, SourcePassage]],
    product: dict[str, Any],
    category: Optional[PartCategory],
) -> dict[str, dict[str, Any]]:
    response = ctx.call_json(
        "compact_obligations",
        COMPACT_OBLIGATIONS_INSTRUCTIONS,
        {
            "part": (
                {"category": category.label, "description": category.description}
                if category
                else None
            ),
            "product": product,
            "paragraphs": [_payload_paragraph(item, passage) for item, passage in batch],
        },
        COMPACT_OBLIGATIONS_SCHEMA,
        prompt_version=PROMPT_VERSIONS["compact_obligations"],
        max_output_tokens=8000,
    )
    asked = {passage.passage_id for _, passage in batch}
    answers: dict[str, dict[str, Any]] = {}
    for entry in response.data.get("paragraphs") or ():
        pid = str(entry.get("passage_id") or "")
        if pid in asked and pid not in answers:
            answers[pid] = entry
    return answers


def _drafts_for(
    item: PlanItem,
    passage: SourcePassage,
    answer: dict[str, Any],
    category_label: str,
) -> list[DraftRequirement]:
    drafts: list[DraftRequirement] = []
    seen: set[str] = set()
    for requirement in (answer.get("requirements") or ())[:MAX_REQUIREMENTS_PER_PARAGRAPH]:
        text = re.sub(r"\s+", " ", str(requirement.get("text") or "")).strip()[:MAX_TEXT_CHARS]
        if not text or text in seen:
            continue
        seen.add(text)
        condition = str(requirement.get("condition") or "").strip() or None
        drafts.append(
            DraftRequirement(
                draft_id=draft_id_for(item.section_id, passage.passage_id, text),
                text=text,
                supporting_passage_ids=(passage.passage_id,),
                primary_passage_id=passage.passage_id,
                obligation_kind=ObligationKind.MANDATORY,
                applicability_condition=condition,
                applicability_reason=f"Judged by the model to apply to {category_label}.",
                values=values_in(passage.text),
                document_id=item.document.document_id,
                section_id=item.section_id,
                prompt_version=PROMPT_VERSIONS["compact_obligations"],
                notes=COMPACT_NOTE,
            )
        )
    return drafts


def compact_extract(
    ctx: RunContext,
    plan: list[PlanItem],
    profile: ProductProfile,
    category: Optional[PartCategory],
) -> dict[str, SectionResult]:
    """Results per section, in the shape the section-by-section path returns."""
    work: list[tuple[PlanItem, SourcePassage]] = []
    inspected: dict[str, list[str]] = {}
    failed: dict[str, str] = {}
    for item in plan:
        try:
            passages = ctx.corpus.get_passages(list(item.section.primary_passage_ids))
        except Exception as exc:
            failed[item.section_id] = f"corpus read failed: {exc}"
            continue
        inspected[item.section_id] = [p.passage_id for p in passages]
        work.extend((item, p) for p in obligation_paragraphs(item, passages))

    batches = _batches(work)
    product = compact_profile(profile)
    label = category.label if category else "this part"
    answers: dict[str, dict[str, Any]] = {}
    unanswered_batches = 0

    ctx.event(
        "extract",
        f"Compact extraction: {len(work)} obligation paragraph(s) from {len(plan)} section(s) "
        f"in {len(batches)} call(s)",
        total=len(batches),
    )
    if batches and not ctx.economy:
        workers = max(1, int(getattr(ctx.config, "max_parallel_sections", 8) or 8))
        done = 0
        with ThreadPoolExecutor(
            max_workers=min(workers, len(batches)), thread_name_prefix="compact"
        ) as pool:
            futures = {pool.submit(_call, ctx, batch, product, category): batch for batch in batches}
            for future in as_completed(futures):
                done += 1
                try:
                    answers.update(future.result())
                except LLMConfigurationError as exc:
                    if getattr(ctx.config, "extraction_mode", "model") not in FALLBACK_MODES:
                        raise
                    switch_to_economy(ctx, exc)
                    unanswered_batches += 1
                except (LLMCallError, ModelBudgetExceeded) as exc:
                    unanswered_batches += 1
                    ctx.warn(
                        f"a compact extraction call failed; its paragraphs fall back to "
                        f"rule-based requirements: {exc}",
                        stage="extract",
                    )
                ctx.event("extract", "Compact batch done", completed=done, total=len(batches))

    by_section: dict[str, list[DraftRequirement]] = {item.section_id: [] for item in plan}
    counts = {"requirement": 0, "not_applicable": 0, "no_obligation": 0, "fallback": 0}
    for item, passage in work:
        answer = answers.get(passage.passage_id)
        if answer is None:
            fallback = economy_requirement(item, passage)
            if fallback is not None:
                by_section[item.section_id].append(fallback)
            counts["fallback"] += 1
            continue
        decision = answer.get("decision")
        counts[decision if decision in counts else "no_obligation"] += 1
        if decision == "requirement":
            by_section[item.section_id].extend(_drafts_for(item, passage, answer, label))

    ctx.event(
        "extract",
        f"Compact extraction: {counts['requirement']} paragraph(s) gave requirements, "
        f"{counts['not_applicable']} do not apply to {label}, {counts['no_obligation']} carry "
        f"no obligation, {counts['fallback']} rule-based"
        + (f" ({unanswered_batches} call(s) unanswered)" if unanswered_batches else ""),
    )

    results: dict[str, SectionResult] = {}
    for item in plan:
        if item.section_id in failed:
            results[item.section_id] = SectionResult(
                drafts=[], inspected_passage_ids=[], resolutions=[], notes=[],
                failed=True, failure_reason=failed[item.section_id],
            )
            continue
        drafts = by_section[item.section_id]
        notes = sorted({d.notes for d in drafts if d.notes} - {None})
        results[item.section_id] = SectionResult(
            drafts=drafts,
            inspected_passage_ids=inspected.get(item.section_id, []),
            resolutions=[],
            notes=list(notes) if notes else [COMPACT_NOTE],
        )
    return results


__all__ = ["compact_extract", "COMPACT_NOTE", "ECONOMY_NOTE"]
