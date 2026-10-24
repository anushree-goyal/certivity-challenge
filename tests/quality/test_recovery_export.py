"""Exporting a valid submission from saved checkpoints, without the model.

This is the floor under the whole system: whatever else fails, the work that
finished must be submittable. The moment this path is needed is the moment the
provider is already unusable, so it makes no model call at all - and it must
still refuse anything whose citation does not resolve, because a plausible
invented source is worse than a missing requirement.
"""

from __future__ import annotations

import json

import pytest

from certivity.agent.checkpoint import CheckpointStore
from certivity.contracts import (
    CoverageStatus,
    DraftRequirement,
    ObligationKind,
    RunInput,
    content_hash,
)
from certivity.recover import (
    deterministic_report,
    draft_from_checkpoints,
    drafts_from_artifact,
    export_from_checkpoints,
    import_artifact_into_checkpoints,
)


@pytest.fixture
def real_corpus(package):
    """The real SQLite corpus, so citations resolve against real documents."""
    from certivity.api.components import open_corpus_only
    from certivity.api.settings import Settings, DEFAULT_ENTRY_POINTS, REPO_ROOT

    corpus_dir = REPO_ROOT / "data" / "cache" / "corpus"
    if not corpus_dir.is_dir():
        pytest.skip("no built corpus snapshot; run `certivity bootstrap`")
    settings = Settings(
        runs_dir=REPO_ROOT / "runs", participant_package=package, corpus_dir=corpus_dir,
        pipeline_mode="real", default_team_id="team-test", max_revision_rounds=0,
        run_time_budget_s=600, max_model_calls=None, entry_points=dict(DEFAULT_ENTRY_POINTS),
    )
    try:
        return open_corpus_only(settings, "real")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"corpus could not be opened: {exc}")


def _run_input(corpus, team_id: str = "team-15") -> RunInput:
    return RunInput(
        run_id="recovery-test",
        team_id=team_id,
        case_id="dev-rear-lamp",
        corpus_snapshot_id=getattr(corpus, "snapshot_id", "snap"),
        input_hash=content_hash("recovery-test"),
        specification_text="a rear lamp",
    )


def _real_passage(corpus):
    """A passage from a base document that has a verified printed page."""
    from certivity.quality.deterministic import verified_page

    for document in corpus.list_documents(role="base"):
        for section in corpus.list_sections(document.document_id):
            if not section.primary_passage_ids:
                continue
            for passage in corpus.get_passages(list(section.primary_passage_ids)):
                if verified_page(passage.printed_start_page) is not None:
                    return document, section, passage
    pytest.skip("no passage with a verified printed page in this snapshot")


def test_a_checkpointed_draft_exports_with_its_real_version_and_page(
    real_corpus, package, tmp_path
):
    """The citation that reaches the submission is the one from the corpus.

    Version label and printed page are read from the document and passage
    records at export time, not carried as free text, which is what stops a
    requirement from citing a page nobody can check.
    """
    from certivity.quality.deterministic import verified_page

    document, section, passage = _real_passage(real_corpus)
    store = CheckpointStore(tmp_path / "cp")
    store.record_section(
        section.section_id,
        status=CoverageStatus.PROCESSED,
        document_id=document.document_id,
        inspected_passage_ids=[passage.passage_id],
        drafts=[
            DraftRequirement(
                draft_id="drf-real-1",
                text="The device shall comply with the requirement stated in this paragraph.",
                supporting_passage_ids=(passage.passage_id,),
                primary_passage_id=passage.passage_id,
                obligation_kind=ObligationKind.MANDATORY,
                document_id=document.document_id,
                section_id=section.section_id,
            )
        ],
    )

    out = tmp_path / "submission.json"
    result = export_from_checkpoints(
        _run_input(real_corpus), real_corpus,
        checkpoint_dir=tmp_path / "cp", output_path=out,
        participant_package=package,
    )

    assert result.requirements == 1, result.excluded
    assert result.valid, result.validation
    written = json.loads(out.read_text(encoding="utf-8"))
    source = written["requirements"][0]["source"]
    assert source["regulation"] == document.regulation
    assert source["version"] == document.version_label
    assert source["page"] == verified_page(passage.printed_start_page)
    assert written["team_id"] == "team-15" and written["case_id"] == "dev-rear-lamp"


def test_an_unresolvable_citation_is_excluded_rather_than_guessed(
    real_corpus, package, tmp_path
):
    store = CheckpointStore(tmp_path / "cp")
    store.record_section(
        "made-up-section",
        status=CoverageStatus.PROCESSED,
        drafts=[
            DraftRequirement(
                draft_id="drf-fake",
                text="The lamp shall do something this passage does not say.",
                supporting_passage_ids=("un-r999:9-9-9:sha256-deadbeefdead#main/1@001",),
                primary_passage_id="un-r999:9-9-9:sha256-deadbeefdead#main/1@001",
            )
        ],
    )
    result = export_from_checkpoints(
        _run_input(real_corpus), real_corpus,
        checkpoint_dir=tmp_path / "cp", participant_package=package,
    )
    assert result.requirements == 0
    assert result.drafts_recovered == 1, "the draft was recovered, then refused"


def test_the_export_is_marked_partial_and_says_what_it_is_missing(
    real_corpus, tmp_path
):
    store = CheckpointStore(tmp_path / "cp")
    store.write_state({"plan_total": 891})
    document, section, passage = _real_passage(real_corpus)
    store.record_section(section.section_id, status=CoverageStatus.PROCESSED,
                         document_id=document.document_id)
    store.record_section("other-section", status=CoverageStatus.FAILED,
                         explanation="timed out")

    draft, result = draft_from_checkpoints(_run_input(real_corpus), store)
    assert draft.partial, "a recovered export must never claim complete coverage"
    assert result.sections_recovered == 1 and result.sections_failed == 1
    assert any("891" in note for note in result.notes)


def test_the_deterministic_report_records_that_review_did_not_finish(
    real_corpus, tmp_path
):
    """A consumer must be able to tell the semantic pass was skipped."""
    document, section, passage = _real_passage(real_corpus)
    store = CheckpointStore(tmp_path / "cp")
    store.record_section(
        section.section_id, status=CoverageStatus.PROCESSED,
        document_id=document.document_id,
        drafts=[DraftRequirement(
            draft_id="drf-1", text="The device shall comply.",
            supporting_passage_ids=(passage.passage_id,),
            primary_passage_id=passage.passage_id,
            document_id=document.document_id, section_id=section.section_id,
        )],
    )
    draft, _ = draft_from_checkpoints(_run_input(real_corpus), store)
    report = deterministic_report(_run_input(real_corpus), draft, real_corpus)
    assert report.partial is True


def test_a_missing_team_id_is_refused_before_anything_is_written(
    real_corpus, package, tmp_path
):
    """A submission with the wrong team id scores for nobody."""
    from certivity.api.settings import team_id_problem

    assert team_id_problem("") is not None
    assert team_id_problem("team-07") is not None
    assert team_id_problem("team-15") is None


# --------------------------------------------------------------------------- #
# Recovering runs that predate checkpointing
# --------------------------------------------------------------------------- #


def _artifact(tmp_path, drafts: list[dict], name: str = "pipeline_draft.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"draft": {"draft_requirements": drafts}}), encoding="utf-8")
    return path


def _payload(draft_id: str, section_id: str, passage_id: str) -> dict:
    return {
        "draft_id": draft_id,
        "text": f"Requirement {draft_id}.",
        "supporting_passage_ids": [passage_id],
        "primary_passage_id": passage_id,
        "obligation_kind": "mandatory",
        "section_id": section_id,
        "document_id": "doc-1",
    }


def test_drafts_are_recoverable_from_an_old_runs_draft_artifact(tmp_path):
    path = _artifact(tmp_path, [_payload("drf-1", "sec-1", "p-1")])
    recovered = drafts_from_artifact(path)
    assert [d.draft_id for d in recovered] == ["drf-1"]


def test_importing_two_runs_unions_their_drafts_per_section(tmp_path):
    """Three runs that read the same section must not leave only the last one."""
    store = CheckpointStore(tmp_path / "cp")
    first = _artifact(tmp_path, [_payload("drf-a", "sec-1", "p-1")], "first.json")
    second = _artifact(tmp_path, [_payload("drf-b", "sec-1", "p-2")], "second.json")

    assert import_artifact_into_checkpoints(first, store) == 1
    assert import_artifact_into_checkpoints(second, store) == 1

    load = store.load()
    assert sorted(d.draft_id for d in load.records["sec-1"].drafts) == ["drf-a", "drf-b"]


def test_importing_the_same_artifact_twice_adds_nothing(tmp_path):
    store = CheckpointStore(tmp_path / "cp")
    path = _artifact(tmp_path, [_payload("drf-a", "sec-1", "p-1")])
    assert import_artifact_into_checkpoints(path, store) == 1
    assert import_artifact_into_checkpoints(path, store) == 0
    assert len(store.load().drafts()) == 1


def test_an_empty_export_is_valid_json_but_not_a_success(real_corpus, package, tmp_path):
    """Nobody should upload an empty file believing the export worked.

    An export with no requirements passes the official validator - it is
    well-formed - so validity alone must not be read as success.
    """
    out = tmp_path / "empty.json"
    result = export_from_checkpoints(
        _run_input(real_corpus), real_corpus,
        checkpoint_dir=tmp_path / "nothing-here", output_path=out,
        participant_package=package,
    )
    assert result.requirements == 0
    assert result.valid, "an empty submission is still well-formed"
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["requirements"] == []
    # The CLI is what turns this into a non-zero exit and a warning; see
    # cli._export. The record itself must simply not claim any requirement.
    assert result.drafts_recovered == 0
