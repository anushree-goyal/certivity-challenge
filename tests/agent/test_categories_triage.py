"""Part categories, reuse across runs, and section triage.

The point of these steps is speed, so the tests check two things: a repeat run
of the same kind of part skips the model calls an earlier run already paid
for, and nothing triage skips disappears from the coverage ledger.
"""

from __future__ import annotations

import json

from certivity.agent import run_extraction
from certivity.agent.categories import corpus_fingerprint
from certivity.contracts import CoverageStatus, LLMCallError, RunConfig, TargetKind
from tests.fixtures.contracts import ScriptedLLMAdapter, default_script
from tests.fixtures.contracts import corpus as syn


def _tasks(llm: ScriptedLLMAdapter) -> list[str]:
    return [call.task for call in llm.calls]


def test_new_category_is_added_and_persisted(run_input, corpus, llm, tmp_path):
    registry = tmp_path / "categories.json"
    run_extraction(run_input, corpus, llm, category_registry_path=registry)

    stored = json.loads(registry.read_text())
    entry = stored["categories"]["rear-signalling-device"]
    assert entry["label"] == "Rear signalling device"
    snapshot = entry["snapshots"][corpus_fingerprint(corpus)]
    assert {d["regulation"] for d in snapshot["applicability"]["decisions"]} == {
        syn.REG_A,
        syn.REG_B,
    }
    assert snapshot["triage"], "triage verdicts were not recorded"


def test_known_category_is_offered_to_the_classifier(run_input, corpus, tmp_path):
    registry = tmp_path / "categories.json"
    run_extraction(run_input, corpus, ScriptedLLMAdapter(script=default_script()),
                   category_registry_path=registry)

    second = ScriptedLLMAdapter(script=default_script())
    run_extraction(run_input, corpus, second, category_registry_path=registry)
    payload = next(c.payload for c in second.calls if c.task == "part_category")
    assert [c["slug"] for c in payload["known_categories"]] == ["rear-signalling-device"]


def test_repeat_run_of_a_category_skips_applicability_and_triage(run_input, corpus, tmp_path):
    registry = tmp_path / "categories.json"
    first = ScriptedLLMAdapter(script=default_script())
    first_draft = run_extraction(run_input, corpus, first, category_registry_path=registry)
    assert "candidate_scope" in _tasks(first)

    second = ScriptedLLMAdapter(script=default_script())
    draft = run_extraction(run_input, corpus, second, category_registry_path=registry)

    tasks = _tasks(second)
    assert "candidate_scope" not in tasks
    assert "document_scope" not in tasks
    assert "section_triage" not in tasks
    assert [d.regulation for d in draft.applicability_decisions] == [
        d.regulation for d in first_draft.applicability_decisions
    ]
    assert all("Reused for part category" in d.explanation for d in draft.applicability_decisions)
    assert len(draft.draft_requirements) == len(first_draft.draft_requirements)


def test_reuse_can_be_switched_off(run_input, corpus, tmp_path):
    registry = tmp_path / "categories.json"
    run_extraction(run_input, corpus, ScriptedLLMAdapter(script=default_script()),
                   category_registry_path=registry)

    fresh_input = run_input.__class__(
        **{**run_input.__dict__, "config": RunConfig(max_model_calls=60, passages_per_batch=4,
                                                    reuse_category_knowledge=False)}
    )
    second = ScriptedLLMAdapter(script=default_script())
    run_extraction(fresh_input, corpus, second, category_registry_path=registry)
    assert "candidate_scope" in _tasks(second)
    assert "section_triage" in _tasks(second)


def test_skipped_sections_stay_in_the_ledger_and_are_not_extracted(run_input, corpus):
    script = default_script()

    def triage(payload):
        return {
            "sections": [
                {
                    "section_id": s["section_id"],
                    "verdict": "skip" if s["section_id"] == syn.A_ANNEX_TEST else "extract",
                    "reason": "test procedure for a different device",
                }
                for s in payload["sections"]
            ]
        }

    script["section_triage"] = triage
    llm = ScriptedLLMAdapter(script=script)
    draft = run_extraction(run_input, corpus, llm)

    by_target = {(c.target_kind, c.target_id): c for c in draft.coverage}
    skipped = by_target[(TargetKind.SECTION, syn.A_ANNEX_TEST)]
    assert skipped.status is CoverageStatus.EXCLUDED
    assert "different device" in skipped.explanation
    extracted = {
        call.payload.get("section", {}).get("section_id")
        for call in llm.calls
        if call.task == "extract_obligations"
    }
    assert syn.A_ANNEX_TEST not in extracted


def test_triage_that_skips_everything_keeps_everything(run_input, corpus):
    script = default_script()
    script["section_triage"] = lambda payload: {
        "sections": [
            {"section_id": s["section_id"], "verdict": "skip", "reason": "x"}
            for s in payload["sections"]
        ]
    }
    llm = ScriptedLLMAdapter(script=script)
    draft = run_extraction(run_input, corpus, llm)

    assert draft.draft_requirements
    assert any("skipped every planned section" in w for w in draft.warnings)
    assert not any(
        c.status is CoverageStatus.EXCLUDED and "triage" in (c.explanation or "")
        for c in draft.coverage
    )


def test_failed_triage_keeps_its_sections(run_input, corpus):
    script = default_script()

    def broken(payload):
        raise LLMCallError("provider returned 529")

    script["section_triage"] = broken
    llm = ScriptedLLMAdapter(script=script)
    draft = run_extraction(run_input, corpus, llm)

    assert draft.draft_requirements
    assert any("triage batch failed" in w for w in draft.warnings)


def test_failed_classification_runs_without_categories(run_input, corpus, tmp_path):
    script = default_script()
    del script["part_category"]
    llm = ScriptedLLMAdapter(script=script)
    registry = tmp_path / "categories.json"
    draft = run_extraction(run_input, corpus, llm, category_registry_path=registry)

    assert draft.draft_requirements
    assert any("part category could not be determined" in w for w in draft.warnings)
    assert not registry.exists()


def test_knowledge_survives_a_rebuilt_snapshot(run_input, corpus, tmp_path):
    """A rebuilt corpus gets a new snapshot ID from the same PDFs; reuse must hold."""
    registry = tmp_path / "categories.json"
    run_extraction(run_input, corpus, ScriptedLLMAdapter(script=default_script()),
                   category_registry_path=registry)

    class Rebuilt:
        snapshot_id = "snap-rebuilt"

        def __getattr__(self, name):
            return getattr(corpus, name)

    rebuilt_input = run_input.__class__(**{**run_input.__dict__, "corpus_snapshot_id": "snap-rebuilt"})
    second = ScriptedLLMAdapter(script=default_script())
    run_extraction(rebuilt_input, Rebuilt(), second, category_registry_path=registry)
    assert "candidate_scope" not in _tasks(second)
    assert "section_triage" not in _tasks(second)
