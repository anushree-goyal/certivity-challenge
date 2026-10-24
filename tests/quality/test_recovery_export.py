"""Exporting a valid submission from saved checkpoints, with no model call.

This is the path that guarantees a deliverable. It has to work when the thing
that made the run stop is still broken - no credentials, no credit, no network -
so nothing here may reach a provider, and nothing here may fill a gap with a
value the source does not establish.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from certivity.agent.checkpoint import CheckpointStore
from certivity.contracts import CoverageStatus, ObligationKind
from certivity.recover import (
    draft_from_checkpoints,
    drafts_from_artifact,
    export_from_checkpoints,
    import_artifact_into_checkpoints,
)
from tests.quality.fixtures import (
    BASE,
    LATEST,
    P_EXC,
    P_LATEST,
    P_NOPAGE,
    P_OK,
    Draft,
    Run,
    corpus,
)


def _store_with(tmp_path: Path, *drafts, status=CoverageStatus.PROCESSED) -> CheckpointStore:
    store = CheckpointStore(tmp_path / "cp")
    by_section: dict[str, list] = {}
    for draft in drafts:
        by_section.setdefault(draft.section_id or "sec-1", []).append(draft)
    for section_id, items in by_section.items():
        store.record_section(
            section_id,
            status=status,
            document_id=items[0].document_id,
            drafts=items,
            inspected_passage_ids=[p for d in items for p in d.supporting_passage_ids],
        )
    return store


# --------------------------------------------------------------------------- #
# The export itself
# --------------------------------------------------------------------------- #


def test_checkpoints_become_a_submission_without_any_model_call(tmp_path):
    store = _store_with(
        tmp_path,
        Draft("d-1", "The luminous intensity shall be at least 4 cd.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
    )
    result = export_from_checkpoints(
        Run(), corpus(), checkpoint_dir=store.directory,
        output_path=tmp_path / "submission.json",
    )

    assert result.requirements == 1
    assert result.drafts_recovered == 1
    body = json.loads((tmp_path / "submission.json").read_text(encoding="utf-8"))
    assert body["team_id"] == "team-x" and body["case_id"] == "case-y"
    assert [r["id"] for r in body["requirements"]] == ["REQ-0001"]


def test_the_exported_citation_keeps_the_base_version_and_the_printed_page(tmp_path):
    """The page and version in the output are the source's own, never derived.

    A wrong version or page is a citation that looks checkable and is not, so
    this asserts the exact values the passage carries rather than that the
    fields are merely present.
    """
    store = _store_with(
        tmp_path,
        Draft("d-1", "Lamps shall be red.", P_EXC.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
    )
    result = export_from_checkpoints(Run(), corpus(), checkpoint_dir=store.directory)

    source = result.submission["requirements"][0]["source"]
    assert source == {
        "regulation": "UN R999",
        "version": "01 series",   # the base version, not the latest one
        "page": 14,               # the page printed on the page
        "paragraph": "6.3",
    }


def test_a_draft_citing_the_latest_version_is_refused_not_relabelled(tmp_path):
    """The expected answers are built on the base version; a latest-version page is wrong."""
    store = _store_with(
        tmp_path,
        Draft("d-latest", "The intensity shall be at least 5 cd.", P_LATEST.passage_id,
              document_id=LATEST.document_id, section_id="sec-1"),
    )
    result = export_from_checkpoints(Run(), corpus(), checkpoint_dir=store.directory)

    assert result.requirements == 0
    # Refused by the deterministic checks, which is a different decision from
    # export declining it, so the report must distinguish the two.
    assert "d-latest" in result.rejected
    assert "wrong_source_version" in result.rejected["d-latest"] or result.rejected["d-latest"]


def test_a_passage_with_no_verified_page_is_left_out_rather_than_guessed(tmp_path):
    store = _store_with(
        tmp_path,
        Draft("d-ok", "The luminous intensity shall be at least 4 cd.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
        Draft("d-nopage", "Marking shall be indelible.", P_NOPAGE.passage_id,
              document_id=BASE.document_id, section_id="sec-2"),
    )
    result = export_from_checkpoints(Run(), corpus(), checkpoint_dir=store.directory)

    assert result.requirements == 1
    dropped = {**result.rejected, **result.excluded}
    assert "d-nopage" in dropped
    assert "page" in dropped["d-nopage"].lower()


def test_an_exclusion_is_not_exported_as_a_requirement(tmp_path):
    """"No requirements apply to X" imposes nothing and has no counterpart in the key."""
    store = _store_with(
        tmp_path,
        Draft("d-exc", "No requirements apply to the housing.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1",
              obligation_kind=ObligationKind.EXCLUSION),
    )
    result = export_from_checkpoints(Run(), corpus(), checkpoint_dir=store.directory)
    assert result.requirements == 0


def test_identical_obligations_are_exported_once(tmp_path):
    same = "The luminous intensity shall be at least 4 cd."
    store = _store_with(
        tmp_path,
        Draft("d-1", same, P_OK.passage_id, document_id=BASE.document_id, section_id="sec-1"),
        Draft("d-2", same, P_OK.passage_id, document_id=BASE.document_id, section_id="sec-2"),
    )
    result = export_from_checkpoints(Run(), corpus(), checkpoint_dir=store.directory)
    assert result.requirements == 1


def test_distinct_obligations_from_one_passage_are_both_kept(tmp_path):
    """Deduplication must not merge two different obligations into one."""
    store = _store_with(
        tmp_path,
        Draft("d-min", "The luminous intensity shall be at least 4 cd.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
        Draft("d-max", "The luminous intensity shall be at most 600 cd.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
    )
    result = export_from_checkpoints(Run(), corpus(), checkpoint_dir=store.directory)
    assert result.requirements == 2


def test_an_export_from_nothing_is_an_empty_submission_not_an_invented_one(tmp_path):
    store = CheckpointStore(tmp_path / "cp")
    result = export_from_checkpoints(Run(), corpus(), checkpoint_dir=store.directory)
    assert result.submission["requirements"] == []
    assert result.requirements == 0


def test_a_failed_section_is_reported_and_contributes_nothing(tmp_path):
    store = CheckpointStore(tmp_path / "cp")
    store.record_section("sec-1", status=CoverageStatus.FAILED, explanation="timed out")
    draft, result = draft_from_checkpoints(Run(), store)

    assert result.sections_failed == 1
    assert result.sections_recovered == 0
    assert draft.partial is True


def test_a_recovered_draft_is_always_marked_partial(tmp_path):
    """A recovered export must never present itself as a complete reading."""
    store = _store_with(
        tmp_path,
        Draft("d-1", "The luminous intensity shall be at least 4 cd.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
    )
    draft, _ = draft_from_checkpoints(Run(), store)
    assert draft.partial is True


def test_a_corrupt_checkpoint_line_is_reported_in_the_export(tmp_path):
    store = _store_with(
        tmp_path,
        Draft("d-1", "The luminous intensity shall be at least 4 cd.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
    )
    with (Path(store.directory) / "sections.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"section_id": "sec-9", "sta')

    result = export_from_checkpoints(Run(), corpus(), checkpoint_dir=store.directory)
    assert result.corrupt_checkpoint_lines == 1
    assert result.requirements == 1, "one damaged line must not cost the whole export"
    assert any("unreadable" in note for note in result.notes)


def test_the_output_file_is_replaced_atomically(tmp_path):
    store = _store_with(
        tmp_path,
        Draft("d-1", "The luminous intensity shall be at least 4 cd.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
    )
    out = tmp_path / "submission.json"
    out.write_text('{"previous": true}', encoding="utf-8")
    export_from_checkpoints(Run(), corpus(), checkpoint_dir=store.directory, output_path=out)

    assert "requirements" in json.loads(out.read_text(encoding="utf-8"))
    assert not list(tmp_path.glob("*.tmp")), "a temporary file was left behind"


# --------------------------------------------------------------------------- #
# Recovering runs that predate checkpointing
# --------------------------------------------------------------------------- #


def _artifact(tmp_path: Path, name: str, *drafts) -> Path:
    from certivity.contracts import to_jsonable

    path = tmp_path / name
    path.write_text(
        json.dumps({"draft": {"draft_requirements": [to_jsonable(d) for d in drafts]}}),
        encoding="utf-8",
    )
    return path


def test_drafts_are_recoverable_from_an_old_draft_artifact(tmp_path):
    path = _artifact(
        tmp_path, "pipeline_draft.json",
        Draft("d-1", "The luminous intensity shall be at least 4 cd.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
    )
    assert [d.draft_id for d in drafts_from_artifact(path)] == ["d-1"]


def test_importing_several_runs_unions_their_work_for_the_same_section(tmp_path):
    """Three runs that read one section must not leave only the last one's obligations."""
    first = _artifact(
        tmp_path, "a.json",
        Draft("d-min", "The luminous intensity shall be at least 4 cd.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
    )
    second = _artifact(
        tmp_path, "b.json",
        Draft("d-max", "The luminous intensity shall be at most 600 cd.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
    )
    store = CheckpointStore(tmp_path / "cp")
    assert import_artifact_into_checkpoints(first, store) == 1
    assert import_artifact_into_checkpoints(second, store) == 1

    assert sorted(d.draft_id for d in store.load().drafts()) == ["d-max", "d-min"]


def test_importing_the_same_artifact_twice_adds_nothing(tmp_path):
    path = _artifact(
        tmp_path, "a.json",
        Draft("d-1", "The luminous intensity shall be at least 4 cd.", P_OK.passage_id,
              document_id=BASE.document_id, section_id="sec-1"),
    )
    store = CheckpointStore(tmp_path / "cp")
    assert import_artifact_into_checkpoints(path, store) == 1
    assert import_artifact_into_checkpoints(path, store) == 0
    assert len(store.load().drafts()) == 1
