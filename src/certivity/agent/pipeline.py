"""The extraction component's public entry points (owner: Anushree).

``run_extraction`` and ``revise_extraction`` are what Teammate 4's composition
layer calls. Both are synchronous and know nothing about HTTP, SQLite, the
submission format or the UI; progress travels through the event callback, and
the component works when that callback does nothing.

Honesty rules this module enforces:

* ``PipelineDraft.partial`` is true unless every planned section ended
  ``PROCESSED`` or ``EXCLUDED``. A failed or pending section makes the run
  partial, whatever else succeeded.
* Coverage records inspection status. It is never a claim of exhaustive legal
  compliance.
* The model budget running out, a corpus read failing or a section failing to
  parse all degrade coverage. None of them silently drop a section from the
  ledger.
* Nothing here reads the golden answer. ``expected.json`` is Teammate 3's
  evaluation module alone.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Sequence

from certivity.contracts import (
    ApplicabilityStatus,
    CorpusReader,
    CoverageItem,
    CoverageStatus,
    EmitEvent,
    LLMAdapter,
    LLMCallError,
    LLMConfigurationError,
    ModelBudgetExceeded,
    PipelineDraft,
    ReferenceResolution,
    ReviewFinding,
    RunInput,
    TargetKind,
    null_emit,
)
from certivity.agent.applicability import (
    apply_consolidation,
    assess_applicability,
    reuse_applicability,
)
from certivity.agent.artifacts import AgentArtifacts
from certivity.agent.categories import (
    PartCategoryRegistry,
    classify_part_category,
    corpus_fingerprint,
)
from certivity.agent.context import RunContext
from certivity.agent.economy import (
    FALLBACK_MODES,
    economy_applicability,
    has_obligation_wording,
    economy_extract_section,
    economy_profile,
    match_category,
    product_terms,
    prune_uncertain,
)
from certivity.agent.extract import extract_section
from certivity.agent.plan import build_inspection_plan
from certivity.agent.profile import build_product_profile, profile_payload
from certivity.agent.prompts import PROMPT_VERSIONS
from certivity.agent.references import ReferenceTracker
from certivity.agent.revise import revise_drafts
from certivity.agent.triage import triage_plan

logger = logging.getLogger(__name__)


def run_extraction(
    run_input: RunInput,
    corpus: CorpusReader,
    llm: LLMAdapter,
    emit_event: EmitEvent = null_emit,
    *,
    artifacts_dir: str | os.PathLike[str] | None = None,
    category_registry_path: str | os.PathLike[str] | None = None,
) -> PipelineDraft:
    """Product understanding, applicability, systematic extraction, references.

    ``category_registry_path`` is the JSON file where part categories and what
    runs learned about them persist. None keeps them in memory for this run.
    """
    ctx = RunContext(
        run_input=run_input, corpus=corpus, llm=llm, emit_event=emit_event
    )
    artifacts = AgentArtifacts(artifacts_dir)
    tracker = ReferenceTracker()

    snapshot_id = getattr(corpus, "snapshot_id", run_input.corpus_snapshot_id)
    if snapshot_id != run_input.corpus_snapshot_id:
        ctx.warn(
            f"the corpus reader is bound to snapshot {snapshot_id!r} but the run input "
            f"names {run_input.corpus_snapshot_id!r}; the reader's snapshot was used",
            stage="agent",
        )

    ctx.event("agent", f"Extraction started for case {run_input.case_id}")

    mode = getattr(ctx.config, "extraction_mode", "model")
    ctx.economy = mode == "economy"
    if not ctx.economy:
        try:
            profile = build_product_profile(ctx)
        except LLMConfigurationError as exc:
            # The model is unusable for this whole run - no credential, no
            # credit, a disabled key. In "auto" mode the run carries on without
            # it; otherwise an empty draft would look like a completed run, so
            # this propagates for the caller to mark the run failed.
            if mode not in FALLBACK_MODES:
                ctx.event(
                    "agent",
                    "Run aborted: the model provider is not usable with this configuration",
                    level="error",
                )
                raise
            _switch_to_economy(ctx, exc)
        except (ModelBudgetExceeded, LLMCallError) as exc:
            # Without a profile there is nothing to decide scope against, so this is
            # the one stage whose failure ends the run.
            ctx.warn(f"the product profile could not be built: {exc}", stage="product_profile")
            return _assemble(
                ctx, snapshot_id, profile=None, decisions=[], drafts=[], coverage=[],
                tracker=tracker, artifacts=artifacts, run_input=run_input,
            )
    if ctx.economy:
        profile = economy_profile(run_input.specification_text)
        ctx.event("product_profile", "Economy mode: product read from the specification text")

    artifacts.write_json("product_profile.json", profile)

    registry = PartCategoryRegistry(category_registry_path)
    corpus_key = corpus_fingerprint(corpus)
    vocabulary = product_terms(profile, run_input.specification_text)
    if ctx.economy:
        category = match_category(ctx, registry, profile, run_input.specification_text)
    else:
        category = classify_part_category(ctx, profile, registry)
    if category is not None:
        artifacts.write_json("part_category.json", category)

    reused = None
    if category is not None and ctx.config.reuse_category_knowledge:
        stored = registry.applicability(category.slug, corpus_key)
        if stored:
            reused = reuse_applicability(ctx, stored, category.label)
    if reused is not None:
        decisions, selected = reused
    elif ctx.economy:
        decisions, selected = economy_applicability(ctx, vocabulary)
    else:
        try:
            decisions, selected = assess_applicability(ctx, profile)
        except LLMConfigurationError as exc:
            if mode not in FALLBACK_MODES:
                raise
            _switch_to_economy(ctx, exc)
            decisions, selected = economy_applicability(ctx, vocabulary)
        except (ModelBudgetExceeded, LLMCallError) as exc:
            ctx.warn(f"applicability assessment stopped early: {exc}", stage="applicability")
            decisions, selected = [], []
        else:
            # Only a complete assessment is worth replaying for the next run.
            if category is not None and decisions and not ctx.budget_exhausted:
                registry.record_applicability(
                    category.slug, corpus_key, decisions, run_input.run_id
                )
    decisions, selected = apply_consolidation(ctx, decisions, selected)
    if ctx.economy or mode == "lite":
        decisions, selected = prune_uncertain(
            ctx, decisions, selected, vocabulary, profile.vehicle_categories
        )
    registry.save()
    artifacts.write_json("applicability.json", decisions)

    if not selected:
        ctx.warn(
            "no document was selected for extraction; no requirement can be produced",
            stage="applicability",
        )

    plan, coverage = build_inspection_plan(ctx, selected, profile)
    conditions_by_document = {
        document_id: decision.conditions
        for decision in decisions
        for document_id in decision.document_ids
        if decision.status in (ApplicabilityStatus.APPLICABLE, ApplicabilityStatus.UNCERTAIN)
    }

    profile_facts = profile_payload(profile)
    drafts = []
    coverage_by_target = {(item.target_kind, item.target_id): item for item in coverage}
    if mode == "lite":
        plan = _drop_sections_without_obligations(ctx, plan, coverage_by_target)
    plan = triage_plan(
        ctx, plan, coverage_by_target, profile, category, registry, corpus_key,
        vocabulary=vocabulary,
    )
    registry.save()

    results = _extract_plan(
        ctx, plan, profile_facts, conditions_by_document, tracker, coverage_by_target
    )
    # Applied in plan order, not completion order, so the output is identical
    # whatever the concurrency and whatever order the provider answers in.
    for item in plan:
        result = results.get(item.section_id)
        if result is not None:
            drafts.extend(result.drafts)

    coverage = _finalise_document_coverage(list(coverage_by_target.values()))
    return _assemble(
        ctx,
        snapshot_id,
        profile=profile,
        decisions=decisions,
        drafts=drafts,
        coverage=coverage,
        tracker=tracker,
        artifacts=artifacts,
        run_input=run_input,
    )


def revise_extraction(
    run_input: RunInput,
    draft: PipelineDraft,
    findings: Sequence[ReviewFinding],
    corpus: CorpusReader,
    llm: LLMAdapter,
    emit_event: EmitEvent = null_emit,
    *,
    artifacts_dir: str | os.PathLike[str] | None = None,
) -> PipelineDraft:
    """Act on review findings and return a new draft.

    Coverage, applicability and the product profile carry over: review findings
    concern obligations, and revising one does not change what was inspected.
    """
    ctx = RunContext(run_input=run_input, corpus=corpus, llm=llm, emit_event=emit_event)
    artifacts = AgentArtifacts(artifacts_dir)
    if getattr(ctx.config, "extraction_mode", "model") == "economy" or any(
        w.startswith("the model cannot be used") for w in draft.warnings
    ):
        # Revision is a model task. Without the model the findings stay open and
        # visible rather than being "resolved" by a call that cannot be made.
        ctx.event("revise", f"Economy mode: {len(findings)} finding(s) left open, no revision call")
        return draft
    ctx.event("revise", f"Revising {len(findings)} finding(s)", total=len(findings))

    revised, notes = revise_drafts(ctx, draft, findings)
    kept_ids = {item.draft_id for item in revised}

    coverage = tuple(
        CoverageItem(
            target_kind=item.target_kind,
            target_id=item.target_id,
            status=item.status,
            explanation=item.explanation,
            inspected_passage_ids=item.inspected_passage_ids,
            draft_ids=tuple(
                draft_id for draft_id in item.draft_ids if draft_id in kept_ids
            ),
            document_id=item.document_id,
        )
        for item in draft.coverage
    )

    result = PipelineDraft(
        run_id=draft.run_id,
        corpus_snapshot_id=draft.corpus_snapshot_id,
        product_profile=draft.product_profile,
        applicability_decisions=draft.applicability_decisions,
        draft_requirements=tuple(revised),
        coverage=coverage,
        unresolved_references=draft.unresolved_references,
        warnings=tuple(dict.fromkeys((*draft.warnings, *ctx.warnings, *notes))),
        usage=draft.usage.merged_with(ctx.usage),
        partial=draft.partial,
        prompt_versions={**draft.prompt_versions, **PROMPT_VERSIONS},
    )
    artifacts.save_draft(result, run_input, name="pipeline_draft_revised.json")
    ctx.event(
        "revise",
        f"Revision complete: {len(result.draft_requirements)} draft(s) remain",
        completed=len(findings),
        total=len(findings),
    )
    return result


def _extract_plan(
    ctx: RunContext,
    plan: list,
    profile_facts: dict,
    conditions_by_document: dict,
    tracker: ReferenceTracker,
    coverage_by_target: dict,
) -> dict:
    """Extract every planned section, up to ``max_parallel_sections`` at a time.

    Concurrency is bounded because the limit is the provider's rate limit, not
    local CPU. Serially the real corpus needs about 6.7 hours of model calls
    against a 5-hour submission window, so this is required rather than an
    optimisation.

    Ordering guarantees: results are keyed by section and applied by the caller
    in plan order, so output does not depend on which call returns first. The
    plan itself is already sorted by expected requirement density, and work is
    submitted in that order, so a run stopped by its budget has spent it on the
    most promising sections.
    """
    workers = max(1, int(getattr(ctx.config, "max_parallel_sections", 1) or 1))
    total = len(plan)
    results: dict[str, object] = {}
    done = 0

    def work(item):
        if ctx.economy:
            return economy_extract_section(ctx, item)
        try:
            return extract_section(
                ctx,
                item,
                profile_facts,
                conditions_by_document.get(item.document.document_id, ()),
                tracker,
            )
        except LLMConfigurationError as exc:
            # The model became unusable mid-run - typically the credit ran out.
            # In "auto" mode the remaining sections are read without it.
            if getattr(ctx.config, "extraction_mode", "model") not in FALLBACK_MODES:
                raise
            _switch_to_economy(ctx, exc)
            return economy_extract_section(ctx, item)

    def record(item, result, error: Optional[BaseException]) -> None:
        nonlocal done
        done += 1
        key = (TargetKind.SECTION, item.section_id)
        if error is not None:
            ctx.warn(f"section {item.section_id} failed: {error}", stage="extract")
            coverage_by_target[key] = CoverageItem(
                target_kind=TargetKind.SECTION,
                target_id=item.section_id,
                status=CoverageStatus.FAILED,
                explanation=f"{type(error).__name__}: {error}",
                document_id=item.document.document_id,
            )
            return
        results[item.section_id] = result
        coverage_by_target[key] = CoverageItem(
            target_kind=TargetKind.SECTION,
            target_id=item.section_id,
            status=CoverageStatus.FAILED if result.failed else CoverageStatus.PROCESSED,
            explanation=result.failure_reason or ("; ".join(result.notes) or None),
            inspected_passage_ids=tuple(result.inspected_passage_ids),
            draft_ids=tuple(d.draft_id for d in result.drafts),
            document_id=item.document.document_id,
        )
        ctx.event(
            "extract",
            f"{item.document.regulation} "
            f"{item.section.paragraph_label or item.section.heading}: "
            f"{len(result.drafts)} obligation(s)",
            completed=done,
            total=total,
            affected_ids=[item.section_id],
        )

    if workers == 1:
        for item in plan:
            if ctx.budget_exhausted:
                break
            try:
                record(item, work(item), None)
            except ModelBudgetExceeded:
                _warn_budget(ctx, done, total)
                break
            except LLMCallError as exc:
                record(item, None, exc)
        return results

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract") as pool:
        pending: dict = {}
        queue = iter(plan)
        stop = False

        def submit_next() -> bool:
            for item in queue:
                pending[pool.submit(work, item)] = item
                return True
            return False

        for _ in range(workers):
            if not submit_next():
                break

        while pending:
            for future in as_completed(list(pending)):
                item = pending.pop(future)
                try:
                    record(item, future.result(), None)
                except ModelBudgetExceeded:
                    if not stop:
                        _warn_budget(ctx, done, total)
                    stop = True
                except LLMCallError as exc:
                    record(item, None, exc)
                if not stop and not ctx.budget_exhausted:
                    submit_next()
                break
        return results


def _drop_sections_without_obligations(
    ctx: RunContext, plan: list, coverage_by_target: dict
) -> list:
    """Lite mode: a section with no "shall"/"must" anywhere is not sent to the model.

    Free to decide and it removes headings, lists and descriptive text before
    any paid call. Such sections stay in the ledger as EXCLUDED with the reason.
    """
    kept = []
    for item in plan:
        if has_obligation_wording(ctx, item):
            kept.append(item)
            continue
        coverage_by_target[(TargetKind.SECTION, item.section_id)] = CoverageItem(
            target_kind=TargetKind.SECTION,
            target_id=item.section_id,
            status=CoverageStatus.EXCLUDED,
            explanation="Not sent to the model (lite mode): no obligation wording (shall/must) in the section.",
            document_id=item.document.document_id,
        )
    if len(kept) < len(plan):
        ctx.event(
            "plan",
            f"Lite mode: {len(plan) - len(kept)} of {len(plan)} section(s) state no obligation "
            "and are not sent to the model",
        )
    return kept


def _switch_to_economy(ctx: RunContext, exc: BaseException) -> None:
    with ctx.lock:
        already = ctx.economy
        ctx.economy = True
    if not already:
        ctx.warn(
            f"the model cannot be used ({exc}); the rest of this run makes no model "
            "calls (economy mode): relevance from saved category knowledge or wording, "
            "one plain-English requirement per obligation paragraph",
            stage="agent",
        )


def _warn_budget(ctx: RunContext, done: int, total: int) -> None:
    ctx.warn(
        f"model budget exhausted after {done} of {total} section(s); the rest are "
        "recorded as pending and coverage is partial",
        stage="extract",
    )


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def _finalise_document_coverage(coverage: list[CoverageItem]) -> tuple[CoverageItem, ...]:
    """Roll section statuses up to their document entries."""
    sections_by_document: dict[str, list[CoverageItem]] = {}
    for item in coverage:
        if item.target_kind is TargetKind.SECTION and item.document_id:
            sections_by_document.setdefault(item.document_id, []).append(item)

    result: list[CoverageItem] = []
    for item in coverage:
        if item.target_kind is not TargetKind.DOCUMENT:
            result.append(item)
            continue
        sections = sections_by_document.get(item.target_id, [])
        counts = {status: 0 for status in CoverageStatus}
        for section in sections:
            counts[section.status] += 1
        if not sections:
            status = CoverageStatus.FAILED
            detail = "no section of this document was inspected"
        elif counts[CoverageStatus.PENDING] or counts[CoverageStatus.FAILED]:
            status = CoverageStatus.PENDING
            detail = (
                f"{counts[CoverageStatus.PROCESSED]} processed, "
                f"{counts[CoverageStatus.PENDING]} pending, "
                f"{counts[CoverageStatus.FAILED]} failed, "
                f"{counts[CoverageStatus.EXCLUDED]} excluded"
            )
        else:
            status = CoverageStatus.PROCESSED
            detail = (
                f"{counts[CoverageStatus.PROCESSED]} section(s) processed, "
                f"{counts[CoverageStatus.EXCLUDED]} excluded"
            )
        result.append(
            CoverageItem(
                target_kind=TargetKind.DOCUMENT,
                target_id=item.target_id,
                status=status,
                explanation=f"{item.explanation}; {detail}" if item.explanation else detail,
                document_id=item.document_id,
            )
        )
    return tuple(result)


def _assemble(
    ctx: RunContext,
    snapshot_id: str,
    *,
    profile,
    decisions,
    drafts,
    coverage,
    tracker: ReferenceTracker,
    artifacts: AgentArtifacts,
    run_input: RunInput,
) -> PipelineDraft:
    from certivity.contracts import ProductProfile

    section_items = [item for item in coverage if item.target_kind is TargetKind.SECTION]
    incomplete = [
        item
        for item in section_items
        if item.status in (CoverageStatus.PENDING, CoverageStatus.FAILED)
    ]
    partial = bool(incomplete) or not section_items or ctx.budget_exhausted

    if incomplete:
        ctx.warn(
            f"{len(incomplete)} of {len(section_items)} planned section(s) were not "
            "processed; coverage is partial",
            stage="agent",
        )

    unresolved: list[ReferenceResolution] = list(tracker.unresolved)

    draft = PipelineDraft(
        run_id=run_input.run_id,
        corpus_snapshot_id=snapshot_id,
        product_profile=profile if profile is not None else ProductProfile(),
        applicability_decisions=tuple(decisions),
        draft_requirements=tuple(drafts),
        coverage=tuple(coverage),
        unresolved_references=tuple(unresolved),
        warnings=tuple(ctx.warnings),
        usage=ctx.usage,
        partial=partial,
        prompt_versions=dict(PROMPT_VERSIONS),
    )

    model_configuration = {}
    describe = getattr(ctx.llm, "describe", None)
    if callable(describe):
        try:
            model_configuration = describe()
        except Exception:  # pragma: no cover
            model_configuration = {}
    artifacts.save_draft(draft, run_input, model_configuration=model_configuration)

    ctx.event(
        "agent",
        f"Extraction finished: {len(draft.draft_requirements)} draft obligation(s), "
        f"coverage {draft.coverage_counts()}, partial={draft.partial}",
    )
    return draft
