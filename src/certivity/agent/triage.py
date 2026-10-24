"""Section triage before extraction (owner: Anushree).

Extraction is the expensive stage: a careful read of every planned section on
the strongest model. Most sections of an applicable regulation are about some
other device or about approval paperwork - a rear lamp needs a few dozen of
R48's 260 sections. Triage asks a small, fast model to sort the plan into
"extract" and "skip" from each section's heading and opening text, in parallel
batches, before any extraction call is made.

Honesty rules:

* A skipped section is never dropped. It stays in the coverage ledger as
  EXCLUDED with the triage reason, so the output states what was not read.
* Any doubt keeps a section: a failed triage call, a section the model did
  not answer for, or a triage that would skip everything.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from certivity.contracts import (
    CoverageItem,
    CoverageStatus,
    LLMCallError,
    LLMConfigurationError,
    ModelBudgetExceeded,
    ProductProfile,
    TargetKind,
)
from certivity.agent.categories import PartCategory, PartCategoryRegistry, compact_profile
from certivity.agent.context import RunContext
from certivity.agent.economy import FALLBACK_MODES, economy_section_verdicts
from certivity.agent.plan import PlanItem
from certivity.agent.prompts import (
    PROMPT_VERSIONS,
    SECTION_TRIAGE_INSTRUCTIONS,
    SECTION_TRIAGE_SCHEMA,
)

EXCERPT_CHARS = 700
"""Opening text shown per section. Enough to see what device and what kind of
rule a section is about; the full text is the extraction step's job."""


def _excerpt(ctx: RunContext, item: PlanItem) -> tuple[str, list[str]]:
    try:
        passages = ctx.corpus.get_passages(list(item.section.primary_passage_ids))
    except Exception:
        passages = []
    text = re.sub(r"\s+", " ", " ".join(p.text for p in passages)).strip()
    ancestry = list(passages[0].heading_ancestry) if passages else []
    return text[:EXCERPT_CHARS], ancestry


def _section_entry(ctx: RunContext, item: PlanItem) -> dict[str, Any]:
    excerpt, ancestry = _excerpt(ctx, item)
    return {
        "section_id": item.section_id,
        "regulation": item.document.regulation,
        "paragraph_label": item.section.paragraph_label,
        "heading": item.section.heading,
        "heading_path": ancestry,
        "text_start": excerpt,
    }


def _triage_batch(
    ctx: RunContext,
    category: Optional[PartCategory],
    product: dict[str, Any],
    batch: list[PlanItem],
) -> dict[str, dict[str, str]]:
    entries = [_section_entry(ctx, item) for item in batch]
    response = ctx.call_json(
        "section_triage",
        SECTION_TRIAGE_INSTRUCTIONS,
        {
            "part_category": (
                {"slug": category.slug, "label": category.label, "description": category.description}
                if category
                else None
            ),
            "product": product,
            "sections": entries,
        },
        SECTION_TRIAGE_SCHEMA,
        prompt_version=PROMPT_VERSIONS["section_triage"],
        max_output_tokens=4000,
    )
    asked = {item.section_id for item in batch}
    verdicts: dict[str, dict[str, str]] = {}
    for entry in response.data.get("sections") or ():
        section_id = str(entry.get("section_id") or "")
        if section_id in asked and entry.get("verdict") in ("extract", "skip"):
            verdicts[section_id] = {
                "verdict": entry["verdict"],
                "reason": str(entry.get("reason") or "").strip(),
            }
    return verdicts


def triage_plan(
    ctx: RunContext,
    plan: list[PlanItem],
    coverage_by_target: dict,
    profile: ProductProfile,
    category: Optional[PartCategory],
    registry: PartCategoryRegistry,
    corpus_key: str,
    *,
    vocabulary: Optional[set[str]] = None,
) -> list[PlanItem]:
    """Return the plan without the sections triage skipped, in the same order.

    Skipped sections are rewritten in ``coverage_by_target`` as EXCLUDED. In
    economy mode, sections with no saved verdict are judged by their heading
    against ``vocabulary``; those verdicts are not saved, so they never stand in
    for a model's judgement on a later run.
    """
    config = ctx.config
    if not getattr(config, "triage_sections", False) or not plan:
        return plan

    reuse = bool(category) and getattr(config, "reuse_category_knowledge", True)
    known = registry.triage_verdicts(category.slug, corpus_key) if reuse else {}
    verdicts: dict[str, dict[str, str]] = {
        item.section_id: known[item.section_id] for item in plan if item.section_id in known
    }
    reused = len(verdicts)
    to_ask = [item for item in plan if item.section_id not in verdicts]

    size = max(1, int(getattr(config, "triage_batch_size", 20) or 20))
    batches = [to_ask[i : i + size] for i in range(0, len(to_ask), size)]
    workers = max(1, int(getattr(config, "max_parallel_triage", 8) or 8))
    product = compact_profile(profile)
    fresh: dict[str, dict[str, str]] = {}
    heuristic: dict[str, dict[str, str]] = {}
    failed_batches = 0

    if batches and ctx.economy:
        heuristic = economy_section_verdicts(ctx, to_ask, vocabulary or set())
        batches = []
    if batches:
        ctx.event(
            "triage",
            f"Triage: screening {len(to_ask)} section(s) in {len(batches)} batch(es)"
            + (f", {reused} reused from earlier runs" if reused else ""),
            total=len(batches),
        )
        done = 0
        with ThreadPoolExecutor(
            max_workers=min(workers, len(batches)), thread_name_prefix="triage"
        ) as pool:
            futures = {
                pool.submit(_triage_batch, ctx, category, product, batch): batch
                for batch in batches
            }
            for future in as_completed(futures):
                done += 1
                try:
                    fresh.update(future.result())
                except LLMConfigurationError as exc:
                    if getattr(config, "extraction_mode", "model") not in FALLBACK_MODES:
                        raise
                    with ctx.lock:
                        ctx.economy = True
                    ctx.warn(
                        f"the model cannot be used for triage ({exc}); sections are "
                        "judged by their headings instead",
                        stage="triage",
                    )
                    heuristic.update(economy_section_verdicts(ctx, futures[future], vocabulary or set()))
                except (LLMCallError, ModelBudgetExceeded) as exc:
                    failed_batches += 1
                    ctx.warn(
                        f"a triage batch failed and its sections are all kept: {exc}",
                        stage="triage",
                    )
                ctx.event("triage", "Triage batch done", completed=done, total=len(batches))

    verdicts.update(fresh)
    verdicts.update(heuristic)
    skipped = [
        item for item in plan if verdicts.get(item.section_id, {}).get("verdict") == "skip"
    ]
    if skipped and len(skipped) == len(plan):
        # Every applicable section skipped means triage misread the product,
        # not that an applicable regulation has nothing in it.
        ctx.warn(
            "triage would have skipped every planned section; all were kept instead",
            stage="triage",
        )
        return plan

    label = category.label if category else "this product"
    for item in skipped:
        reason = verdicts[item.section_id].get("reason") or "no requirement on this part"
        source = (
            "reused triage" if item.section_id in known
            else "economy heading check" if item.section_id in heuristic
            else "triage"
        )
        coverage_by_target[(TargetKind.SECTION, item.section_id)] = CoverageItem(
            target_kind=TargetKind.SECTION,
            target_id=item.section_id,
            status=CoverageStatus.EXCLUDED,
            explanation=f"Not extracted ({source} for {label}): {reason}",
            document_id=item.document.document_id,
        )

    if category and fresh:
        registry.record_triage(category.slug, corpus_key, fresh)

    skipped_ids = {item.section_id for item in skipped}
    kept = [item for item in plan if item.section_id not in skipped_ids]
    ctx.event(
        "triage",
        f"Triage kept {len(kept)} of {len(plan)} section(s) for extraction; "
        f"{len(skipped)} skipped"
        + (f", {failed_batches} batch(es) failed and were kept" if failed_batches else ""),
        completed=len(kept),
        total=len(plan),
    )
    return kept
