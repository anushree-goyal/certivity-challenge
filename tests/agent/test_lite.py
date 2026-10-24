"""Lite mode: interpretation by the small model, with nothing obvious sent to it."""

from __future__ import annotations

import dataclasses

from certivity.agent import run_extraction
from certivity.agent.economy import ECONOMY_NOTE
from certivity.contracts import CoverageStatus, LLMConfigurationError
from tests.fixtures.contracts import ScriptedLLMAdapter, default_script
from tests.fixtures.contracts import corpus as syn


def _lite(run_input):
    return dataclasses.replace(
        run_input, config=dataclasses.replace(run_input.config, extraction_mode="lite")
    )


def _extracted_sections(llm):
    return {
        call.payload["section"]["section_id"]
        for call in llm.calls
        if call.task == "extract_obligations"
    }


def test_lite_mode_requirements_come_from_the_model(run_input, corpus):
    llm = ScriptedLLMAdapter(script=default_script())
    draft = run_extraction(_lite(run_input), corpus, llm)

    assert draft.draft_requirements
    assert "extract_obligations" in [c.task for c in llm.calls]
    # Interpreted by the model, not the rule-based economy path.
    assert all(d.notes != ECONOMY_NOTE for d in draft.draft_requirements)


def test_lite_mode_never_sends_a_section_without_obligation_wording(run_input, corpus):
    def has_obligation(section_id):
        texts = [p.text.lower() for p in syn.PASSAGES if p.section_id == section_id]
        return any("shall" in t or "must" in t for t in texts)

    full = ScriptedLLMAdapter(script=default_script())
    run_extraction(run_input, corpus, full)
    lite = ScriptedLLMAdapter(script=default_script())
    draft = run_extraction(_lite(run_input), corpus, lite)

    sent = _extracted_sections(lite)
    assert all(has_obligation(s) for s in sent)
    skipped = _extracted_sections(full) - sent
    by_target = {c.target_id: c for c in draft.coverage}
    for section_id in skipped:
        assert by_target[section_id].status is CoverageStatus.EXCLUDED
        assert "no obligation wording" in by_target[section_id].explanation


def test_lite_mode_falls_back_to_economy_without_credit(run_input, corpus):
    class NoCredit(ScriptedLLMAdapter):
        def generate_json(self, *args, **kwargs):
            raise LLMConfigurationError("Your credit balance is too low")

    draft = run_extraction(_lite(run_input), corpus, NoCredit(script={}))
    assert draft.draft_requirements
    assert any(w.startswith("the model cannot be used") for w in draft.warnings)


def test_lite_preset_selects_the_small_model(monkeypatch):
    from certivity.llm import adapter_from_env

    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "lite")
    monkeypatch.delenv("CERTIVITY_MODEL", raising=False)
    monkeypatch.delenv("CERTIVITY_MODEL_EFFORT", raising=False)
    adapter = adapter_from_env()
    assert adapter.model_for("extract_obligations") == "claude-haiku-5-5"
    assert adapter.effort_for("extract_obligations") == "medium"

    monkeypatch.setenv("CERTIVITY_MODEL", "claude-sonnet-5-5")
    assert adapter_from_env().model_for("extract_obligations") == "claude-sonnet-5-5"
