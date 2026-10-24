"""Obligation extraction from one section (owner: Anushree).

The unit of work is a batch of primary passages from one section, supplied
together with its inherited conditions, relevant definitions and table context.
After the first pass, any cross reference the model says it needs is resolved
and the batch is re-read with those targets available.

Citation integrity is enforced here, in code, not left to the prompt. An
obligation whose primary citation is not a passage that was actually supplied
is dropped, because a fabricated source id is worse than a missing
requirement: it looks checkable and is not.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from certivity.contracts import (
    DraftRequirement,
    ReferenceRequest,
    NumericValue,
    ObligationKind,
    ReferenceResolution,
    ReferenceStatus,
    SectionBundle,
    SourcePassage,
    content_hash,
)
from certivity.agent.context import RunContext
from certivity.agent.plan import PlanItem
from certivity.agent.profile import profile_payload
from certivity.agent.prompts import (
    EXTRACT_OBLIGATIONS_INSTRUCTIONS,
    EXTRACT_OBLIGATIONS_SCHEMA,
    PROMPT_VERSIONS,
)
from certivity.agent.references import (
    ReferenceTracker,
    follow_references,
    requests_from_extraction,
)

MAX_PARALLEL_BATCHES = 4
"""Concurrent batches inside one section. Most sections are one batch; this
bounds how many extra calls a large annex adds on top of the section workers."""


@dataclass
class SectionResult:
    """What inspecting one section produced."""

    drafts: list[DraftRequirement]
    inspected_passage_ids: list[str]
    resolutions: list[ReferenceResolution]
    notes: list[str]
    failed: bool = False
    failure_reason: str | None = None


def draft_id_for(section_id: str, primary_passage_id: str, text: str) -> str:
    """Content-derived id, so a re-run of the same input yields the same ids.

    Stable ids matter because review findings, duplicate maps and revisions all
    address drafts by id across process boundaries.
    """
    return "drf-" + content_hash(section_id, primary_passage_id, text.strip())[:12]


def _passage_payload(passages: Iterable[SourcePassage], role: str) -> list[dict[str, Any]]:
    return [
        {
            "passage_id": p.passage_id,
            "role": role,
            "namespace": p.namespace,
            "paragraph_label": p.paragraph_label,
            "heading_ancestry": list(p.heading_ancestry),
            "printed_page": p.printed_start_page,
            "is_table": p.is_table,
            "table_context": p.table_context,
            "parsing_flags": list(p.parsing_flags),
            "text": p.text,
        }
        for p in passages
    ]


def _batches(passages: Sequence[SourcePassage], size: int) -> list[list[SourcePassage]]:
    size = max(1, size)
    return [list(passages[i : i + size]) for i in range(0, len(passages), size)]


def extract_section(
    ctx: RunContext,
    item: PlanItem,
    profile_facts: dict[str, Any],
    applicability_conditions: Sequence[str],
    tracker: ReferenceTracker,
) -> SectionResult:
    """Inspect one section and return its draft obligations."""
    try:
        bundle = ctx.corpus.read_section(item.section_id, include_context=True)
    except Exception as exc:
        ctx.warn(
            f"section {item.section_id} could not be read from the corpus: {exc}",
            stage="extract",
        )
        return SectionResult(
            drafts=[],
            inspected_passage_ids=[],
            resolutions=[],
            notes=[],
            failed=True,
            failure_reason=f"corpus read failed: {exc}",
        )

    if not bundle.primary_passages:
        return SectionResult(
            drafts=[],
            inspected_passage_ids=[],
            resolutions=[],
            notes=["the corpus returned no primary passages for this section"],
        )

    drafts: list[DraftRequirement] = []
    inspected: list[str] = []
    resolutions: list[ReferenceResolution] = []
    notes: list[str] = []

    all_batches = _batches(bundle.primary_passages, ctx.config.passages_per_batch)
    cap = max(1, int(getattr(ctx.config, "max_batches_per_section", 4) or 4))
    if len(all_batches) > cap:
        # Bound the tail: one section must not hold a worker indefinitely while
        # the rest of the plan waits. The truncation is recorded, not hidden.
        notes.append(
            f"section truncated after {cap} of {len(all_batches)} batches "
            f"({len(bundle.primary_passages)} passages); raise "
            "RunConfig.max_batches_per_section to read all of it"
        )
        ctx.warn(
            f"section {item.section_id} has {len(all_batches)} batches; only the "
            f"first {cap} were read",
            stage="extract",
        )
        all_batches = all_batches[:cap]

    def run(batch):
        return _extract_batch(
            ctx,
            item,
            bundle,
            batch,
            profile_facts,
            applicability_conditions,
            tracker,
        )

    # A section's batches are independent reads of different passages, so they
    # run concurrently; serially, one large annex was the last thing a run
    # waited for. Results are taken in batch order, and the first failure
    # propagates exactly as it did serially.
    if len(all_batches) == 1:
        outcomes = [run(all_batches[0])]
    else:
        with ThreadPoolExecutor(
            max_workers=min(MAX_PARALLEL_BATCHES, len(all_batches)),
            thread_name_prefix="batch",
        ) as pool:
            futures = [pool.submit(run, batch) for batch in all_batches]
            outcomes = [future.result() for future in futures]

    for batch, (batch_drafts, batch_resolutions, batch_notes) in zip(all_batches, outcomes):
        drafts.extend(batch_drafts)
        resolutions.extend(batch_resolutions)
        notes.extend(batch_notes)
        inspected.extend(p.passage_id for p in batch)

    return SectionResult(
        drafts=drafts,
        inspected_passage_ids=inspected,
        resolutions=resolutions,
        notes=notes,
    )


def _extract_batch(
    ctx: RunContext,
    item: PlanItem,
    bundle: SectionBundle,
    batch: list[SourcePassage],
    profile_facts: dict[str, Any],
    applicability_conditions: Sequence[str],
    tracker: ReferenceTracker,
) -> tuple[list[DraftRequirement], list[ReferenceResolution], list[str]]:
    section = bundle.section
    context_passages = list(bundle.context_passages)
    reference_context: list[SourcePassage] = []
    resolutions: list[ReferenceResolution] = []
    notes: list[str] = []
    drafts: list[DraftRequirement] = []

    for pass_index in range(ctx.config.max_reference_depth + 1):
        payload = {
            "product": profile_facts,
            "regulation": item.document.regulation,
            "version_label": item.document.version_label,
            "regulation_applies_when": list(applicability_conditions),
            "section": {
                "section_id": section.section_id,
                "heading": section.heading,
                "paragraph_label": section.paragraph_label,
                "namespace": section.namespace,
                "parsing_flags": list(section.parsing_flags),
            },
            "primary_passages": _passage_payload(batch, "primary"),
            "context_passages": _passage_payload(context_passages, "context"),
            "referenced_passages": _passage_payload(reference_context, "referenced"),
            "corpus_notes": list(bundle.notes),
        }
        response = ctx.call_json(
            "extract_obligations",
            EXTRACT_OBLIGATIONS_INSTRUCTIONS,
            payload,
            EXTRACT_OBLIGATIONS_SCHEMA,
            prompt_version=PROMPT_VERSIONS["extract_obligations"],
        )
        data = response.data

        allowed = {p.passage_id for p in batch}
        allowed.update(p.passage_id for p in context_passages)
        allowed.update(p.passage_id for p in reference_context)
        primary_ids = {p.passage_id for p in batch}

        drafts = _build_drafts(
            ctx,
            data.get("obligations") or (),
            section_id=section.section_id,
            document_id=item.document.document_id,
            allowed_passage_ids=allowed,
            primary_candidate_ids=primary_ids,
        )
        notes.extend(str(note) for note in data.get("needs_context") or ())
        if data.get("section_note"):
            notes.append(str(data["section_note"]))

        requests = requests_from_extraction(
            data.get("references") or (),
            document_id=item.document.document_id,
            valid_passage_ids=allowed,
            ctx=ctx,
        )
        # Add the references the corpus detected itself. Relying only on the
        # model to notice a cross reference makes a miss silent; a parser pass
        # over the same passages does not have that failure mode.
        requests = _merge_corpus_candidates(ctx, requests, batch)
        new_requests = [r for r in requests if not tracker.seen(r)]
        if not new_requests or pass_index >= ctx.config.max_reference_depth:
            # Record the resolutions of references we are not chasing further, so
            # a draft can still state that its target was never inspected.
            for request in requests:
                known = tracker.get(request)
                if known is not None:
                    resolutions.append(known)
            break

        fetched, new_resolutions = follow_references(
            ctx, new_requests, tracker, depth=pass_index + 1
        )
        resolutions.extend(new_resolutions)
        if not fetched:
            break
        reference_context.extend(fetched)
        ctx.event(
            "extract",
            f"Re-reading {section.section_id} with "
            f"{len(reference_context)} referenced passage(s)",
            affected_ids=[section.section_id],
        )

    resolutions = _dedupe_resolutions(resolutions)
    unresolved = tuple(r for r in resolutions if r.status is not ReferenceStatus.RESOLVED)
    if unresolved:
        drafts = [_with_unresolved(draft, unresolved) for draft in drafts]
    return drafts, resolutions, notes


def _dedupe_resolutions(
    resolutions: list[ReferenceResolution],
) -> list[ReferenceResolution]:
    """One entry per reference target.

    A section re-read after following its references sees the same references
    again, so without this the same unresolved target would be reported several
    times on one draft.
    """
    by_key: dict[str, ReferenceResolution] = {}
    for resolution in resolutions:
        by_key.setdefault(resolution.request.key(), resolution)
    return list(by_key.values())


def _merge_corpus_candidates(
    ctx: RunContext,
    requests: list[ReferenceRequest],
    batch: list[SourcePassage],
) -> list[ReferenceRequest]:
    """Union of the model's references and the corpus's detected candidates.

    Deduplicated on ``ReferenceRequest.key()``, so a reference both found is
    followed once. A corpus that does not implement detection simply contributes
    nothing.
    """
    lister = getattr(ctx.corpus, "list_reference_candidates", None)
    if not callable(lister):
        return requests
    try:
        detected = list(lister([p.passage_id for p in batch]))
    except Exception as exc:
        ctx.warn(
            f"the corpus could not list reference candidates: {exc}", stage="references"
        )
        return requests

    by_key = {r.key(): r for r in requests}
    added = 0
    for candidate in detected:
        if candidate.key() not in by_key:
            by_key[candidate.key()] = candidate
            added += 1
    if added:
        ctx.event(
            "references",
            f"The corpus detected {added} cross reference(s) the extraction did not report",
        )
    return list(by_key.values())


def _with_unresolved(
    draft: DraftRequirement,
    unresolved: tuple[ReferenceResolution, ...],
) -> DraftRequirement:
    """Attach only the unresolved references this draft's own evidence raised.

    Scoping by the draft's cited passages rather than by the batch matters: a
    colour requirement must not inherit an unresolved reference that appeared in
    a neighbouring paragraph about test procedures. Every unresolved target is
    still reported at run level through the tracker, so nothing is lost.
    """
    supporting = set(draft.supporting_passage_ids)
    relevant = tuple(
        resolution
        for resolution in unresolved
        if resolution.request.originating_passage_id in supporting
    )
    if not relevant:
        return draft
    return DraftRequirement(
        draft_id=draft.draft_id,
        text=draft.text,
        supporting_passage_ids=draft.supporting_passage_ids,
        primary_passage_id=draft.primary_passage_id,
        obligation_kind=draft.obligation_kind,
        applicability_condition=draft.applicability_condition,
        applicability_reason=draft.applicability_reason,
        exceptions=draft.exceptions,
        values=draft.values,
        unresolved_references=relevant,
        document_id=draft.document_id,
        section_id=draft.section_id,
        prompt_version=draft.prompt_version,
        revision_of=draft.revision_of,
        notes=draft.notes,
    )


def _build_drafts(
    ctx: RunContext,
    entries: Iterable[dict[str, Any]],
    *,
    section_id: str,
    document_id: str,
    allowed_passage_ids: set[str],
    primary_candidate_ids: set[str],
) -> list[DraftRequirement]:
    """Convert model output into records, dropping anything uncitable."""
    drafts: list[DraftRequirement] = []
    seen_ids: set[str] = set()
    dropped_out_of_scope: list[str] = []

    for entry in entries:
        text = str(entry.get("text") or "").strip()
        primary = str(entry.get("primary_passage_id") or "").strip()
        if not text:
            continue
        if primary not in allowed_passage_ids:
            ctx.warn(
                f"an obligation in {section_id} cited primary passage {primary!r}, which "
                "was not supplied to the model; the obligation was dropped",
                stage="extract",
            )
            continue

        supporting = [
            pid
            for pid in dict.fromkeys(entry.get("supporting_passage_ids") or ())
            if pid in allowed_passage_ids
        ]
        dropped = [
            pid for pid in entry.get("supporting_passage_ids") or () if pid not in allowed_passage_ids
        ]
        if dropped:
            ctx.warn(
                f"an obligation in {section_id} listed {len(dropped)} unsupplied "
                "supporting passage id(s); they were dropped from its evidence",
                stage="extract",
            )
        if primary not in supporting:
            supporting.insert(0, primary)

        reach = str(entry.get("product_applicability") or "applies")
        if reach == "does_not_apply":
            # The source's own scope puts this product outside the provision -
            # typically a section written for a different device category. A real
            # obligation, but not this product's. Offline scoring of a real R148
            # run found 12 front-lamp obligations emitted for a rear lamp, one of
            # which said in its own reason that it did not reach the product.
            #
            # "depends_on_unknown" is deliberately NOT dropped: uncertainty about
            # a product fact is not proof that a rule is irrelevant.
            dropped_out_of_scope.append(
                str(entry.get("applicability_reason") or "no reason given")
            )
            continue

        notes = entry.get("notes")
        if reach == "depends_on_unknown":
            marker = "Applicability depends on an unestablished product fact."
            notes = f"{notes} {marker}".strip() if notes else marker
        if primary not in primary_candidate_ids:
            # The obligation belongs to the section that owns this passage, and
            # that section is read in its own right. Keeping it here as well
            # produced the same requirement twice under two citations in a live
            # run, so it is dropped rather than flagged.
            ctx.warn(
                f"an obligation in {section_id} was cited to {primary!r}, a context "
                "or referenced passage rather than a primary passage of this "
                "section; it was dropped because the section owning that passage "
                "is inspected separately",
                stage="extract",
            )
            continue

        try:
            kind = ObligationKind(entry.get("obligation_kind") or "mandatory")
        except ValueError:
            kind = ObligationKind.MANDATORY

        draft_id = draft_id_for(section_id, primary, text)
        if draft_id in seen_ids:
            # Identical text from the same citation inside one batch is a repeat,
            # not two obligations. Teammate 3 still owns cross-section dedup.
            continue
        seen_ids.add(draft_id)

        drafts.append(
            DraftRequirement(
                draft_id=draft_id,
                text=text,
                supporting_passage_ids=tuple(supporting),
                primary_passage_id=primary,
                obligation_kind=kind,
                applicability_condition=entry.get("applicability_condition"),
                applicability_reason=entry.get("applicability_reason"),
                exceptions=tuple(entry.get("exceptions") or ()),
                values=tuple(
                    NumericValue(
                        quantity=value.get("quantity"),
                        value=value.get("value"),
                        unit=value.get("unit"),
                        comparator=value.get("comparator"),
                    )
                    for value in entry.get("values") or ()
                ),
                document_id=document_id,
                section_id=section_id,
                prompt_version=PROMPT_VERSIONS["extract_obligations"],
                notes=notes,
            )
        )

    if dropped_out_of_scope:
        ctx.warn(
            f"{len(dropped_out_of_scope)} obligation(s) in {section_id} were excluded "
            "because the source's own scope does not reach this product: "
            + "; ".join(dropped_out_of_scope[:3]),
            stage="extract",
        )
    return drafts
