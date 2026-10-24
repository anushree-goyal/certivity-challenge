"""Stand-in "real" modules built on dataclasses, bound through entry points like the owners' modules.

They check that the app hands each module the records another module returned (not JSON dicts),
and that real-mode wiring works without fixture_mode. Not a substitute for the owners' code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from certivity.api import fixture_mode


@dataclass
class DrawingRef:
    drawing_id: str
    media_type: str
    asset_ref: str
    caption: str | None = None


@dataclass
class RunInput:
    # Field order and required/optional split as in contracts.py 1.0.0 (Anushree).
    run_id: str
    team_id: str
    case_id: str
    corpus_snapshot_id: str | None
    input_hash: str
    specification_text: str | None = None
    drawings: tuple[DrawingRef, ...] = ()
    config: Any = None

    def __post_init__(self):
        if not self.specification_text and not self.drawings:
            raise ValueError("RunInput needs specification_text or drawings")


def make_run_input(*, run_id, team_id, case_id, corpus_snapshot_id, specification_text=None,
                   drawing_paths=(), config=None, input_hash=None):
    drawings = tuple(DrawingRef(Path(p).stem, "image/" + Path(p).suffix.lstrip(".").replace("jpg", "jpeg"), str(p))
                     for p in drawing_paths)
    return RunInput(run_id, team_id, case_id, corpus_snapshot_id, input_hash or "computed",
                    specification_text, drawings, config)


@dataclass
class Finding:
    draft_id: str
    category: str
    explanation: str
    supporting_passage_ids: list[str]
    requested_context: str | None = None


@dataclass
class PipelineDraft:
    product_profile: dict[str, Any]
    applicability_decisions: list[dict[str, Any]]
    draft_requirements: list[dict[str, Any]]
    coverage: list[dict[str, Any]]
    unresolved_references: list[Any]
    warnings: list[str]
    usage: dict[str, Any]


@dataclass
class QualityReport:
    accepted_draft_ids: list[str]
    findings: list[Finding]
    duplicate_mapping: dict[str, str]
    rejected: list[dict[str, str]]
    remaining_issues: list[str] = field(default_factory=list)
    export_mapping: dict[str, str] = field(default_factory=dict)


CALLS: list[str] = []


def open_corpus(corpus_dir):
    corpus = fixture_mode.FixtureCorpus()
    corpus.snapshot_id = "fake-snapshot-1"
    return corpus


class FakeLLM:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir

    def describe(self):
        return {"credentials_configured": CREDENTIALS[0], "credential_source": "test", "model": "fake-model",
                "calls_used": 0, "max_calls": None}


CREDENTIALS = [True]
REVIEW_INCOMPLETE = [False]
SEEN: dict[str, Any] = {}


def create_llm(*, cache_dir=None, max_calls=None, model_overrides=None, timeout_s=None, max_retries=None):
    SEEN["cache_dir"] = cache_dir
    return FakeLLM(cache_dir)


def run_extraction(run_input, corpus, llm, emit_event, artifacts_dir=None):
    assert isinstance(run_input, RunInput), type(run_input)
    assert run_input.drawings and run_input.drawings[0].media_type == "image/png"
    SEEN["artifacts_dir"] = artifacts_dir
    assert run_input.specification_text and run_input.corpus_snapshot_id == "fake-snapshot-1"
    CALLS.append("extract")
    emit_event(stage="extraction", message="fake extraction", completed=1, total=1)
    return PipelineDraft(**fixture_mode._draft(revised=False))


def revise_extraction(run_input, draft, findings, corpus, llm, emit_event):
    assert isinstance(draft, PipelineDraft) and all(isinstance(f, Finding) for f in findings), (type(draft), findings)
    CALLS.append(f"revise:{len(findings)}")
    return PipelineDraft(**fixture_mode._draft(revised=True))


def review_requirements(run_input, draft, corpus, llm, emit_event):
    assert isinstance(draft, PipelineDraft), type(draft)
    CALLS.append("review")
    raw = fixture_mode.review_requirements(run_input, asdict(draft), corpus, llm, emit_event)
    findings = [Finding(**f) for f in raw["findings"]]
    remaining = list(raw["remaining_issues"])
    if REVIEW_INCOMPLETE[0]:
        remaining.append({"category": "review_incomplete", "explanation": "no model credential"})
    return QualityReport(raw["accepted_draft_ids"], findings, raw["duplicate_mapping"], raw["rejected"], remaining)


def build_submission(run_input, draft, report, corpus):
    assert isinstance(report, QualityReport)
    return fixture_mode.build_submission(
        {"team_id": run_input.team_id, "case_id": run_input.case_id}, asdict(draft),
        {"accepted_draft_ids": report.accepted_draft_ids}, corpus)


def validate_submission(submission, participant_package_path):
    return fixture_mode.validate_submission(submission, participant_package_path)


@dataclass
class SubmissionResult:
    submission: dict[str, Any]
    export_mapping: dict[str, str]
    excluded: list[dict[str, str]]
    blocking_issues: list[str]


def build_submission_detailed(run_input, draft, report, corpus):
    submission = build_submission(run_input, draft, report, corpus)
    # Deliberately not export order: proves the app uses export_mapping.
    ids = {d: f"R-{i:03d}" for i, d in enumerate(report.accepted_draft_ids, 1)}
    for req, draft_id in zip(submission["requirements"], report.accepted_draft_ids):
        req["id"] = f"Q-{draft_id}"
        ids[draft_id] = req["id"]
    return SubmissionResult(submission, ids, [{"draft_id": "D-3", "reason": "duplicate"}], [])
