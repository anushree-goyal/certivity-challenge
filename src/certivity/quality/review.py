"""review_requirements: deterministic checks -> exact duplicates -> model review -> near duplicates.

An accepted ID always means "passed every check that actually ran". What did not run (no model,
budget, malformed or failed reviews, unresolved duplicate candidates) is listed in remaining_issues
and the review_incomplete finding names the affected draft IDs. Review is never marked complete
when it is not.
"""
from __future__ import annotations

from . import _access as A
from ._records import emit, make_finding, make_report
from .dedup import exact_duplicates, near_duplicates
from .deterministic import NEEDS_REVISION, check_drafts
from .semantic import (SEM_CONTEXT, SEM_REVISION, SemanticConfig, review_semantically)

REVIEW_INCOMPLETE = "review_incomplete"


def review_requirements(run_input, draft, corpus, llm, emit_event, config: SemanticConfig | None = None):
    emit(emit_event, "quality", "Deterministic checks started")
    det = check_drafts(run_input, draft, corpus)
    drafts = {A.draft_id(d): d for d in A.drafts_of(draft)}
    findings = list(det.findings)
    rejected = dict(det.rejected)
    needs_rev = dict(det.needs_revision)

    exact = exact_duplicates(drafts, det.accepted_ids)
    findings += exact.findings
    candidates = [i for i in det.accepted_ids if i not in exact.mapping]

    emit(emit_event, "quality", f"{len(candidates)} drafts to review semantically",
         completed=0, total=len(candidates))
    sem = review_semantically(draft, candidates, corpus, llm, config)
    findings += sem.findings
    rejected.update(sem.rejected)
    needs_rev.update(sem.needs_revision)
    survivors = [i for i in candidates if i not in sem.rejected and i not in sem.needs_revision]

    passages = {A.passage_id(p): p for p in corpus.get_passages(
        sorted({A.draft_primary_id(drafts[i]) for i in survivors if A.draft_primary_id(drafts[i])}))}
    near = near_duplicates(drafts, survivors, passages, llm)
    findings += near.findings
    dup_map = {**exact.mapping, **near.mapping}
    accepted = [i for i in survivors if i not in near.mapping]

    remaining = [f for f in findings if f.draft_id in needs_rev
                 and f.category in (NEEDS_REVISION | {SEM_REVISION, SEM_CONTEXT})]
    gaps = []
    if sem.unreviewed:
        gaps.append(f"{len(sem.unreviewed)} draft(s) not semantically reviewed: {sorted(sem.unreviewed)}")
    if near.unresolved:
        gaps.append(f"unresolved near-duplicate candidates: {near.unresolved}")
    if llm is None:
        gaps.append("no model adapter: semantic review and near-duplicate confirmation NOT performed")
    if sem.warnings:
        gaps.append("warnings: " + "; ".join(sem.warnings))
    if gaps:
        remaining.append(make_finding(None, REVIEW_INCOMPLETE, " | ".join(gaps)))
    remaining += [f for f in near.findings if f.category == "possible_duplicate_unresolved"]

    emit(emit_event, "quality",
         f"Review {'PARTIAL' if gaps else 'complete'}: {len(accepted)} accepted, {len(rejected)} rejected, "
         f"{len(needs_rev)} need revision, {len(dup_map)} duplicates removed, "
         f"{len(sem.unreviewed)} unreviewed; model calls {sem.calls + near.calls}"
         + ("; semantic review NOT performed" if llm is None else ""),
         completed=len(candidates), total=len(candidates))
    return make_report(accepted, findings, dup_map, rejected, remaining)
