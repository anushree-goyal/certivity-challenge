"""Build a valid submission from whatever extraction has finished so far.

The problem this solves: a run that had read 15 sections of 891 was holding
every obligation it had found in memory, and an interruption threw all of it
away. The pipeline could only produce output by finishing. With section
checkpoints on disk, the newest saved state is always exportable, so there is a
submittable file at every point in a run rather than only at the end.

What this does *not* do is invent anything. It exports the drafts that are
checkpointed, with the citations they were extracted with, through the same
deterministic checks and the same official export code the full pipeline uses.
A gap in coverage shows up as fewer requirements, never as a filled-in guess.

The semantic (model-driven) part of review is deliberately skipped: this path
must work with no provider, no credentials and no budget, because the moment it
is needed most is the moment something has already gone wrong.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from certivity.agent.checkpoint import CheckpointStore, checkpoint_dir_for
from certivity.contracts import (
    ApplicabilityStatus,
    CoverageItem,
    CoverageStatus,
    PipelineDraft,
    ProductProfile,
    QualityReport,
    RunInput,
    TargetKind,
)


@dataclass
class RecoveryResult:
    """What an export from checkpoints produced."""

    submission: dict[str, Any] = field(default_factory=dict)
    validation: Optional[dict[str, Any]] = None
    sections_recovered: int = 0
    sections_failed: int = 0
    drafts_recovered: int = 0
    requirements: int = 0
    excluded: dict[str, str] = field(default_factory=dict)
    blocking_issues: list[str] = field(default_factory=list)
    corrupt_checkpoint_lines: int = 0
    checkpoint_dir: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return bool(self.validation and self.validation.get("valid"))

    def summary(self) -> dict[str, Any]:
        return {
            "checkpoint_dir": self.checkpoint_dir,
            "sections_recovered": self.sections_recovered,
            "sections_failed": self.sections_failed,
            "drafts_recovered": self.drafts_recovered,
            "requirements": self.requirements,
            "valid": self.valid,
            "excluded": len(self.excluded),
            "blocking_issues": self.blocking_issues,
            "corrupt_checkpoint_lines": self.corrupt_checkpoint_lines,
            "notes": self.notes,
        }


def draft_from_checkpoints(
    run_input: RunInput,
    store: CheckpointStore,
    *,
    profile: ProductProfile | None = None,
) -> tuple[PipelineDraft, RecoveryResult]:
    """Rebuild a ``PipelineDraft`` from saved section records.

    The draft is marked ``partial`` whenever any recorded section failed or any
    checkpoint line was unreadable, so a recovered export never presents itself
    as a complete reading of the corpus.
    """
    load = store.load()
    result = RecoveryResult(
        checkpoint_dir=str(store.directory) if store.directory else None,
        corrupt_checkpoint_lines=load.corrupt_lines,
    )

    coverage: list[CoverageItem] = []
    for section_id in sorted(load.records):
        record = load.records[section_id]
        try:
            status = CoverageStatus(record.status)
        except ValueError:
            status = CoverageStatus.FAILED
        if status is CoverageStatus.FAILED:
            result.sections_failed += 1
        else:
            result.sections_recovered += 1
        coverage.append(
            CoverageItem(
                target_kind=TargetKind.SECTION,
                target_id=section_id,
                status=status,
                explanation=record.explanation,
                inspected_passage_ids=tuple(record.inspected_passage_ids),
                draft_ids=tuple(d.draft_id for d in record.drafts),
                document_id=record.document_id,
            )
        )

    drafts = load.drafts()
    result.drafts_recovered = len(drafts)
    if load.corrupt_lines:
        result.notes.append(
            f"{load.corrupt_lines} checkpoint record(s) were unreadable and were "
            "skipped; the sections they held are not represented in this export"
        )

    state = store.read_state()
    planned = state.get("plan_total")
    if planned:
        result.notes.append(
            f"{result.sections_recovered} of {planned} planned section(s) are "
            "represented in this export"
        )

    draft = PipelineDraft(
        run_id=run_input.run_id,
        corpus_snapshot_id=run_input.corpus_snapshot_id,
        product_profile=profile if profile is not None else ProductProfile(),
        applicability_decisions=(),
        draft_requirements=tuple(drafts),
        coverage=tuple(coverage),
        warnings=tuple(result.notes),
        partial=True,
    )
    return draft, result


def deterministic_report(run_input, draft, corpus) -> QualityReport:
    """Review without the model: structural checks plus exact-duplicate removal.

    This is the subset of review that needs no provider. It still enforces the
    checks that protect citation integrity - a draft whose primary passage is
    missing, out of snapshot or unverifiable is rejected here exactly as it
    would be in a full run - so a recovered submission is held to the same
    standard, just without the semantic judgement a model adds.
    """
    from certivity.quality import _access as A
    from certivity.quality.dedup import exact_duplicates
    from certivity.quality.deterministic import check_drafts

    det = check_drafts(run_input, draft, corpus)
    drafts = {A.draft_id(d): d for d in A.drafts_of(draft)}
    accepted_ids = list(det.accepted_ids)
    exact = exact_duplicates(drafts, accepted_ids)
    accepted = [i for i in accepted_ids if i not in exact.mapping]
    return QualityReport(
        run_id=getattr(run_input, "run_id", "recovered"),
        accepted_draft_ids=tuple(accepted),
        findings=tuple([*det.findings, *exact.findings]),
        duplicate_mapping=dict(exact.mapping),
        rejected=dict(det.rejected),
        # True without exception: the semantic half of review did not run, and
        # a consumer of this report must be able to tell that from the record
        # rather than from a comment.
        partial=True,
    )


def export_from_checkpoints(
    run_input: RunInput,
    corpus,
    *,
    checkpoint_dir: str | os.PathLike[str] | None = None,
    output_path: str | os.PathLike[str] | None = None,
    participant_package: str | os.PathLike[str] | None = None,
    prompt_versions: Any = None,
) -> RecoveryResult:
    """Checkpoints in, validated submission file out. No model call is made."""
    directory = checkpoint_dir or checkpoint_dir_for(
        run_input, None, None, prompt_versions=prompt_versions
    )
    if directory is None:
        raise ValueError(
            "no checkpoint directory was given and none could be derived; pass "
            "checkpoint_dir explicitly"
        )
    store = CheckpointStore(directory)
    draft, result = draft_from_checkpoints(run_input, store)

    from certivity.quality.export import build_submission_detailed

    report = deterministic_report(run_input, draft, corpus)
    built = build_submission_detailed(run_input, draft, report, corpus)
    result.submission = built.submission
    result.excluded = dict(built.excluded)
    result.blocking_issues = list(built.blocking_issues)
    result.requirements = len(built.submission.get("requirements") or ())

    if participant_package is not None:
        from certivity.quality.validate import validate_submission

        validation = validate_submission(result.submission, participant_package)
        result.validation = (
            validation if isinstance(validation, dict) else _as_dict(validation)
        )

    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written through a temporary file: an export that is interrupted must
        # not leave a half-written submission where a valid one used to be.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(result.submission, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)

    return result


def _as_dict(record: Any) -> dict[str, Any]:
    from certivity.contracts import to_jsonable

    value = to_jsonable(record)
    return value if isinstance(value, dict) else {"valid": False, "errors": [str(record)]}

def drafts_from_artifact(path: str | os.PathLike[str]) -> list[Any]:
    """Read drafts out of a ``pipeline_draft.json`` written by an earlier run.

    Runs that predate section checkpointing left their obligations only in this
    artifact. It is the same record, so the same export path can read it, and
    work from those runs is recoverable rather than lost. Drafts that cannot be
    reconstructed are skipped, for the reason given in
    ``checkpoint._draft_from_payload``: a requirement whose citation no longer
    resolves is worse than a missing one.
    """
    from certivity.agent.checkpoint import _draft_from_payload

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    draft = payload.get("draft", payload)
    out = []
    for item in draft.get("draft_requirements") or ():
        rebuilt = _draft_from_payload(item)
        if rebuilt is not None:
            out.append(rebuilt)
    return out


def import_artifact_into_checkpoints(
    path: str | os.PathLike[str], store: CheckpointStore
) -> int:
    """Merge an old run's drafts into a checkpoint store, grouped by section.

    Unions with whatever the store already holds for each section, rather than
    replacing it. The store's own rule is last-writer-wins per section, which is
    right during a run - a retried section supersedes its earlier outcome - but
    wrong for importing several runs that read the same sections: three runs
    that each read UN R148 5.1.1 would leave only the last one's obligations.
    Recovery wants the union, so the record this writes already contains it.

    Idempotent either way: section ids and draft ids are both content-derived,
    so importing the same artifact twice adds nothing.
    """
    drafts = drafts_from_artifact(path)
    existing = store.load().records
    by_section: dict[str, list[Any]] = {}
    for draft in drafts:
        by_section.setdefault(draft.section_id or "unknown-section", []).append(draft)

    imported = 0
    for section_id, section_drafts in sorted(by_section.items()):
        merged: dict[str, Any] = {}
        prior = existing.get(section_id)
        passages: set[str] = set()
        notes: list[str] = []
        if prior is not None:
            for draft in prior.drafts:
                merged[draft.draft_id] = draft
            passages.update(prior.inspected_passage_ids)
            notes.extend(prior.notes)
        for draft in section_drafts:
            if draft.draft_id not in merged:
                imported += 1
            merged[draft.draft_id] = draft
            passages.update(draft.supporting_passage_ids)
        note = f"recovered from an earlier run's draft artifact: {path}"
        if note not in notes:
            notes.append(note)
        store.record_section(
            section_id,
            status=CoverageStatus.PROCESSED,
            document_id=section_drafts[0].document_id,
            explanation=f"imported from {Path(path).name}",
            inspected_passage_ids=sorted(passages),
            drafts=list(merged.values()),
            notes=notes,
        )
    return imported
