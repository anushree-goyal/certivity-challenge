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
from certivity.agent.applicability import assess_applicability
from certivity.agent.artifacts import AgentArtifacts
from certivity.agent.context import RunContext
from certivity.agent.extract import extract_section
from certivity.agent.plan import build_inspection_plan
from certivity.agent.profile import build_product_profile, profile_payload
from certivity.agent.prompts import PROMPT_VERSIONS
from certivity.agent.references import ReferenceTracker
from certivity.agent.revise import revise_drafts

logger = logging.getLogger(__name__)


def run_extraction(
    run_input: RunInput,
    corpus: CorpusReader,
    llm: LLMAdapter,
    emit_event: EmitEvent = null_emit,
    *,
    artifacts_dir: str | os.PathLike[str] | None = None,
) -> PipelineDraft:
    """Product understanding, applicability, systematic extraction, references."""
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

    try:
        profile = build_product_profile(ctx)
    except LLMConfigurationError:
        # The model is unusable for this whole run - no credential, no credit,
        # a disabled key. Continuing would produce an empty draft that looks
        # like a completed run, so this propagates for the caller to mark the
        # run failed with the provider's own message.
        ctx.event(
            "agent",
            "Run aborted: the model provider is not usable with this configuration",
            level="error",
        )
        raise
    except (ModelBudgetExceeded, LLMCallError) as exc:
        # Without a profile there is nothing to decide scope against, so this is
        # the one stage whose failure ends the run.
        ctx.warn(f"the product profile could not be built: {exc}", stage="product_profile")
        return _assemble(
            ctx, snapshot_id, profile=None, decisions=[], drafts=[], coverage=[],
            tracker=tracker, artifacts=artifacts, run_input=run_input,
        )

    artifacts.write_json("product_profile.json", profile)

    try:
        decisions, selected = assess_applicability(ctx, profile)
    except (ModelBudgetExceeded, LLMCallError) as exc:
        ctx.warn(f"applicability assessment stopped early: {exc}", stage="applicability")
        decisions, selected = [], []
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
    total = len(plan)

    for position, item in enumerate(plan, start=1):
        if ctx.budget_exhausted:
            break
        try:
            result = extract_section(
                ctx,
                item,
                profile_facts,
                conditions_by_document.get(item.document.document_id, ()),
                tracker,
            )
        except ModelBudgetExceeded:
            ctx.warn(
                f"model budget exhausted after {position - 1} of {total} section(s); "
                "the rest are recorded as pending and coverage is partial",
                stage="extract",
            )
            break
        except LLMCallError as exc:
            # One unusable section must not lose the sections after it.
            ctx.warn(f"section {item.section_id} failed: {exc}", stage="extract")
            result = None

        key = (TargetKind.SECTION, item.section_id)
        if result is None:
            coverage_by_target[key] = CoverageItem(
                target_kind=TargetKind.SECTION,
                target_id=item.section_id,
                status=CoverageStatus.FAILED,
                explanation="the model call for this section did not produce usable output",
                document_id=item.document.document_id,
            )
            continue

        drafts.extend(result.drafts)
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
            f"{item.document.regulation} {item.section.paragraph_label or item.section.heading}: "
            f"{len(result.drafts)} obligation(s)",
            completed=position,
            total=total,
            affected_ids=[item.section_id],
        )

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
