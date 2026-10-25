"""Lite mode: compact, paragraph-level interpretation on the small model."""

from __future__ import annotations

import dataclasses
import re

from certivity.agent import run_extraction
from certivity.agent.compact import COMPACT_NOTE
from certivity.agent.economy import ECONOMY_NOTE
from certivity.contracts import CoverageStatus, LLMCallError, LLMConfigurationError
from tests.fixtures.contracts import ScriptedLLMAdapter, default_script
from tests.fixtures.contracts import corpus as syn

OBLIGATION = re.compile(r"\b(shall|must)\b", re.I)


def _lite(run_input):
    return dataclasses.replace(
        run_input, config=dataclasses.replace(run_input.config, extraction_mode="lite")
    )


def _sent_paragraphs(llm):
    return [
        p for call in llm.calls if call.task == "compact_obligations" for p in call.payload["paragraphs"]
    ]


def test_lite_mode_requirements_come_from_the_model(run_input, corpus):
    llm = ScriptedLLMAdapter(script=default_script())
    draft = run_extraction(_lite(run_input), corpus, llm)

    assert draft.draft_requirements
    assert all(d.notes == COMPACT_NOTE for d in draft.draft_requirements)
    assert "extract_obligations" not in [c.task for c in llm.calls]
    known = {p.passage_id for p in syn.PASSAGES}
    for d in draft.draft_requirements:
        assert d.primary_passage_id in known and d.supporting_passage_ids == (d.primary_passage_id,)


def test_lite_mode_sends_only_obligation_paragraphs_in_few_calls(run_input, corpus):
    llm = ScriptedLLMAdapter(script=default_script())
    draft = run_extraction(_lite(run_input), corpus, llm)

    sent = _sent_paragraphs(llm)
    assert sent and all(OBLIGATION.search(p["text"]) for p in sent)
    calls = [c for c in llm.calls if c.task == "compact_obligations"]
    assert len(calls) < len({p["passage_id"] for p in sent}) or len(sent) == 1
    for c in draft.coverage:
        if c.status is CoverageStatus.EXCLUDED and "no obligation wording" in (c.explanation or ""):
            assert not any(p.section_id == c.target_id and OBLIGATION.search(p.text) for p in syn.PASSAGES)


def test_not_applicable_paragraphs_give_no_requirement(run_input, corpus):
    script = default_script()
    script["compact_obligations"] = lambda payload: {
        "paragraphs": [
            {"passage_id": p["passage_id"], "decision": "not_applicable", "requirements": []}
            for p in payload["paragraphs"]
        ]
    }
    draft = run_extraction(_lite(run_input), corpus, ScriptedLLMAdapter(script=script))
    assert draft.draft_requirements == ()


def test_a_failed_call_falls_back_to_rule_based_requirements(run_input, corpus):
    script = default_script()

    def broken(payload):
        raise LLMCallError("provider returned 529")

    script["compact_obligations"] = broken
    draft = run_extraction(_lite(run_input), corpus, ScriptedLLMAdapter(script=script))
    assert draft.draft_requirements
    assert all(d.notes == ECONOMY_NOTE for d in draft.draft_requirements)
    assert any("compact extraction call failed" in w for w in draft.warnings)


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
    monkeypatch.delenv("CERTIVITY_MODEL_EXTRACT", raising=False)
    adapter = adapter_from_env()
    assert adapter.model_for("compact_obligations") == "claude-haiku-5-5"
    assert adapter.effort_for("compact_obligations") == "medium"

    monkeypatch.setenv("CERTIVITY_MODEL_EXTRACT", "claude-sonnet-5-5")
    assert adapter_from_env().model_for("compact_obligations") == "claude-sonnet-5-5"
