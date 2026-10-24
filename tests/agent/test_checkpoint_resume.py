"""Checkpointing, resume and recovery.

These cover the failure that motivated the whole mechanism: a run that had
extracted real obligations was interrupted and kept none of them, because
nothing reached disk until the run finished. The behaviour asserted here is
what makes that impossible - every finished section is saved immediately, a
second run skips what the first completed, and a saved checkpoint is
exportable on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from certivity.agent import run_extraction
from certivity.agent.checkpoint import (
    CheckpointStore,
    checkpoint_dir_for,
    checkpoint_key,
)
from certivity.contracts import (
    CoverageStatus,
    DraftRequirement,
    LLMConfigurationError,
    NumericValue,
    ObligationKind,
    RunConfig,
    RunInput,
    TargetKind,
    content_hash,
)
from tests.fixtures.contracts import corpus as syn


# --------------------------------------------------------------------------- #
# The store itself
# --------------------------------------------------------------------------- #


def _draft(draft_id: str = "drf-aaa", text: str = "The lamp shall emit red light.") -> DraftRequirement:
    return DraftRequirement(
        draft_id=draft_id,
        text=text,
        supporting_passage_ids=("p-1", "p-2"),
        primary_passage_id="p-1",
        obligation_kind=ObligationKind.MANDATORY,
        applicability_condition="when fitted to an M1 vehicle",
        values=(NumericValue(quantity="intensity", value="4", unit="cd", comparator=">="),),
        document_id="doc-1",
        section_id="sec-1",
    )


def test_a_saved_section_round_trips_exactly(tmp_path):
    store = CheckpointStore(tmp_path)
    draft = _draft()
    store.record_section(
        "sec-1", status=CoverageStatus.PROCESSED, document_id="doc-1",
        inspected_passage_ids=["p-1", "p-2"], drafts=[draft], notes=["read in one batch"],
    )

    record = store.load().records["sec-1"]
    assert record.completed
    # Equality, not a field-by-field approximation: a draft that comes back
    # subtly different is a citation that no longer matches its source.
    assert record.drafts == (draft,)
    assert record.inspected_passage_ids == ("p-1", "p-2")


def test_replaying_a_section_does_not_duplicate_its_drafts(tmp_path):
    store = CheckpointStore(tmp_path)
    for _ in range(3):
        store.record_section("sec-1", status=CoverageStatus.PROCESSED, drafts=[_draft()])

    load = store.load()
    assert len(load.records) == 1
    assert [d.draft_id for d in load.drafts()] == ["drf-aaa"]


def test_a_truncated_final_line_costs_one_section_not_the_file(tmp_path):
    """A process killed mid-write leaves a partial line. The rest must survive."""
    store = CheckpointStore(tmp_path)
    store.record_section("sec-1", status=CoverageStatus.PROCESSED, drafts=[_draft()])
    store.record_section("sec-2", status=CoverageStatus.PROCESSED, drafts=[_draft("drf-bbb")])
    with (tmp_path / "sections.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"v":1,"section_id":"sec-3","status":"proc')

    load = store.load()
    assert load.corrupt_lines == 1
    assert sorted(load.records) == ["sec-1", "sec-2"]
    assert len(load.drafts()) == 2


def test_a_draft_that_cannot_be_rebuilt_is_dropped_not_repaired(tmp_path):
    """A citation that will not reconstruct must not become a guessed one."""
    path = tmp_path / "sections.jsonl"
    path.write_text(
        json.dumps(
            {
                "v": 1,
                "section_id": "sec-1",
                "status": "processed",
                "drafts": [
                    {"draft_id": "drf-ok", "text": "ok", "primary_passage_id": "p-1",
                     "supporting_passage_ids": ["p-1"]},
                    {"draft_id": "drf-broken", "text": "no primary passage"},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    load = CheckpointStore(tmp_path).load()
    assert [d.draft_id for d in load.drafts()] == ["drf-ok"]


def test_a_failed_section_is_recorded_but_not_treated_as_done(tmp_path):
    store = CheckpointStore(tmp_path)
    store.record_section("sec-1", status=CoverageStatus.FAILED, explanation="timed out")

    record = store.load().records["sec-1"]
    assert not record.completed, "a failure must be retried on resume, not skipped"
    assert record.explanation == "timed out"


def test_state_survives_an_interrupted_write(tmp_path):
    store = CheckpointStore(tmp_path)
    store.write_state({"plan_total": 891})
    store.write_state({"plan_total": 856})
    assert store.read_state() == {"plan_total": 856}
    assert not list(tmp_path.glob("*.tmp")), "no temporary file may be left behind"


def test_a_disabled_store_is_a_working_no_op(tmp_path):
    store = CheckpointStore(None)
    store.record_section("sec-1", status=CoverageStatus.PROCESSED, drafts=[_draft()])
    assert store.load().records == {}
    assert store.enabled is False


# --------------------------------------------------------------------------- #
# Where checkpoints live
# --------------------------------------------------------------------------- #


def _run_input(**overrides) -> RunInput:
    fields = {
        "run_id": "run-1",
        "team_id": "team-test",
        "case_id": "dev-rear-lamp",
        "corpus_snapshot_id": "snap-abc",
        "input_hash": content_hash("input"),
        "specification_text": "a rear lamp",
    }
    fields.update(overrides)
    return RunInput(**fields)


def test_the_key_identifies_the_work_not_the_run():
    """Two attempts at the same case must land on the same checkpoints."""
    first = _run_input(run_id="run-1")
    second = _run_input(run_id="run-2")
    assert checkpoint_key(first) == checkpoint_key(second)


@pytest.mark.parametrize("field,value", [
    ("case_id", "heldout-other"),
    ("corpus_snapshot_id", "snap-different"),
    ("input_hash", content_hash("a different spec")),
])
def test_the_key_changes_when_the_work_changes(field, value):
    assert checkpoint_key(_run_input()) != checkpoint_key(_run_input(**{field: value}))


def test_a_prompt_change_invalidates_checkpoints():
    """Resuming past a prompt change would keep obligations the new prompt would not produce."""
    run_input = _run_input()
    assert checkpoint_key(run_input, {"extract_obligations": "v7"}) != checkpoint_key(
        run_input, {"extract_obligations": "v8"}
    )


def test_without_a_place_to_write_nothing_is_checkpointed():
    """The agent writes only where the application told it to."""
    assert checkpoint_dir_for(_run_input()) is None


def test_off_confines_checkpoints_to_the_run_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("CERTIVITY_CHECKPOINT_DIR", "off")
    assert checkpoint_dir_for(_run_input(), None, tmp_path) == tmp_path / "checkpoint"


def test_a_configured_base_is_keyed_by_the_work(monkeypatch, tmp_path):
    monkeypatch.setenv("CERTIVITY_CHECKPOINT_DIR", str(tmp_path))
    resolved = checkpoint_dir_for(_run_input(), None, tmp_path / "agent")
    assert resolved.parent == tmp_path
    assert resolved.name == checkpoint_key(_run_input())


# --------------------------------------------------------------------------- #
# Resume through the real pipeline
# --------------------------------------------------------------------------- #


def test_a_resumed_run_skips_finished_sections_and_makes_no_call(
    run_input, corpus, llm, tmp_path
):
    """The point of the whole mechanism: completed work is not paid for twice."""
    artifacts = tmp_path / "agent"
    checkpoints = tmp_path / "cp"

    first = run_extraction(
        run_input, corpus, llm, artifacts_dir=artifacts, checkpoint_dir=checkpoints
    )
    assert first.draft_requirements
    calls_after_first = len(llm.calls)

    saved = CheckpointStore(checkpoints).load()
    assert saved.records, "the first run saved nothing"

    second = run_extraction(
        run_input, corpus, llm, artifacts_dir=artifacts, checkpoint_dir=checkpoints
    )
    extraction_calls = [
        task for task in llm.tasks_called()[calls_after_first:]
        if task == "extract_obligations"
    ]
    assert extraction_calls == [], "a resumed run re-read sections it had already finished"

    # And the answer is the same, not merely cheaper.
    assert {d.draft_id for d in second.draft_requirements} == {
        d.draft_id for d in first.draft_requirements
    }


def test_a_resumed_run_produces_no_duplicate_requirements(
    run_input, corpus, llm, tmp_path
):
    checkpoints = tmp_path / "cp"
    run_extraction(run_input, corpus, llm, artifacts_dir=tmp_path / "a", checkpoint_dir=checkpoints)
    second = run_extraction(
        run_input, corpus, llm, artifacts_dir=tmp_path / "a", checkpoint_dir=checkpoints
    )
    ids = [d.draft_id for d in second.draft_requirements]
    assert len(ids) == len(set(ids))


def test_an_interrupted_run_keeps_the_sections_it_finished(
    run_input, corpus, script, tmp_path
):
    """The real case: a run stops part-way and the finished work survives.

    Stopping it on the model-call budget is the same shape of event as running
    out of credit: some sections are done, the rest are not. What matters is
    that the done ones are on disk and the unread ones stay visible.
    """
    from tests.fixtures.contracts import ScriptedLLMAdapter

    checkpoints = tmp_path / "cp"
    # Three calls go to the profile and applicability, so this finishes some
    # sections and not others.
    starved = ScriptedLLMAdapter(script=script, max_calls=5)
    partial = run_extraction(
        run_input, corpus, starved, artifacts_dir=tmp_path / "a", checkpoint_dir=checkpoints
    )
    assert partial.partial, "a budget-stopped run must report itself partial"
    assert any("budget" in w for w in partial.warnings), partial.warnings

    saved = CheckpointStore(checkpoints).load()
    assert saved.completed_ids, "an interrupted run saved no finished section"
    # Everything the run returned is on disk, which is what makes it exportable.
    returned = {d.draft_id for d in partial.draft_requirements}
    assert returned <= {d.draft_id for d in saved.drafts()}

    # And finishing the job later only costs the sections that were left.
    resumed = run_extraction(
        run_input, corpus, ScriptedLLMAdapter(script=script),
        artifacts_dir=tmp_path / "a", checkpoint_dir=checkpoints,
    )
    assert len(resumed.draft_requirements) >= len(partial.draft_requirements)
    assert not resumed.partial, "the resumed run should have finished the plan"


def test_a_provider_that_dies_mid_run_does_not_discard_finished_sections(
    run_input, corpus, llm, tmp_path
):
    """Out of credit after some sections: keep them, report partial, do not raise.

    Discarding the finished sections to raise sooner is how a run with real
    requirements in hand ends up exporting nothing.
    """
    checkpoints = tmp_path / "cp"
    calls = {"n": 0}
    original = llm.generate_json

    def failing(task, *args, **kwargs):
        if task == "extract_obligations":
            calls["n"] += 1
            if calls["n"] > 2:
                raise LLMConfigurationError(
                    "the model is not usable with this account's configuration: "
                    "credit balance is too low"
                )
        return original(task, *args, **kwargs)

    llm.generate_json = failing  # type: ignore[method-assign]
    draft = run_extraction(
        run_input, corpus, llm, artifacts_dir=tmp_path / "a", checkpoint_dir=checkpoints
    )

    assert draft.partial
    assert any("unusable" in w for w in draft.warnings), draft.warnings
    assert CheckpointStore(checkpoints).load().completed_ids, (
        "the sections read before the provider failed were thrown away"
    )
