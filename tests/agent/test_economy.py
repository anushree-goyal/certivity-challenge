"""Economy mode: a complete run with no model call, and the plain-English rules."""

from __future__ import annotations

import dataclasses

from certivity.agent import run_extraction
from certivity.agent.economy import plain_english
from certivity.contracts import CoverageStatus, LLMConfigurationError
from tests.fixtures.contracts import ScriptedLLMAdapter, default_script
from tests.fixtures.contracts import corpus as syn


class NoCredit(ScriptedLLMAdapter):
    def generate_json(self, *args, **kwargs):
        self.calls.append(args[0] if args else kwargs.get("task"))
        raise LLMConfigurationError("Your credit balance is too low")


def _economy(run_input):
    return dataclasses.replace(
        run_input, config=dataclasses.replace(run_input.config, extraction_mode="economy")
    )


def test_economy_mode_makes_no_model_call_and_cites_every_draft(run_input, corpus):
    llm = NoCredit(script={})
    draft = run_extraction(_economy(run_input), corpus, llm)

    assert llm.calls == [], "economy mode must not call the model"
    assert draft.draft_requirements
    known = {p.passage_id: p for p in syn.PASSAGES}
    for item in draft.draft_requirements:
        passage = known[item.primary_passage_id]
        assert item.supporting_passage_ids == (item.primary_passage_id,)
        assert "shall" not in item.text.lower()
        assert "shall" in passage.text.lower() or "must" in passage.text.lower()
        assert "without a model call" in (item.notes or "")


def test_auto_mode_finishes_without_credit(run_input, corpus):
    draft = run_extraction(run_input, corpus, NoCredit(script={}))

    assert draft.draft_requirements, "auto mode must not end with an empty draft"
    assert any(w.startswith("the model cannot be used") for w in draft.warnings)


def test_auto_mode_switches_when_the_credit_runs_out_mid_run(run_input, corpus):
    script = default_script()

    def out_of_credit(payload):
        raise LLMConfigurationError("Your credit balance is too low")

    script["extract_obligations"] = out_of_credit
    draft = run_extraction(run_input, corpus, ScriptedLLMAdapter(script=script))

    assert draft.draft_requirements
    assert not [c for c in draft.coverage if c.status is CoverageStatus.FAILED]


def test_economy_reuses_saved_category_knowledge(run_input, corpus, tmp_path):
    registry = tmp_path / "categories.json"
    run_extraction(run_input, corpus, ScriptedLLMAdapter(script=default_script()),
                   category_registry_path=registry)

    economy_input = dataclasses.replace(
        _economy(run_input), specification_text="Rear signalling device rear lamp for M1 cars."
    )
    draft = run_extraction(economy_input, corpus, NoCredit(script={}), category_registry_path=registry)
    assert all("Reused for part category" in d.explanation for d in draft.applicability_decisions)


def test_plain_english_keeps_values_and_terms():
    source = (
        "5.2.1. Notwithstanding paragraph 5.1., the lamp shall, in accordance with Annex 4, "
        "emit not less than 6.0∙10^1 cd for category M1 vehicles, provided that it is "
        "reciprocally incorporated; it shall not exceed 140 cm^2."
    )
    text = plain_english(source)
    assert text.startswith("Despite paragraph 5.1.")
    assert "must, as set out in Annex 4" in text
    assert "60 cd" in text and "140 cm^2" in text
    assert "category M1" in text and "reciprocally incorporated" in text
    assert "as long as" in text and "must not exceed" in text
    assert "shall" not in text.lower() and "5.2.1" not in text


def test_superseded_regulation_is_compared_but_not_cited(run_input):
    from types import SimpleNamespace

    from certivity.agent.applicability import apply_consolidation
    from certivity.agent.context import RunContext
    from certivity.contracts import ApplicabilityDecision, ApplicabilityStatus

    intro = SimpleNamespace(
        text="This Regulation combines the provisions of individual UN Regulations Nos. 6, 7 "
        "and 23 into a single Regulation."
    )

    class Corpus:
        def list_sections(self, document_id):
            if document_id == "new":
                return [SimpleNamespace(heading="Introduction (for information)", primary_passage_ids=("i",))]
            return []

        def get_passages(self, ids):
            return [intro]

    docs = [SimpleNamespace(document_id=i, regulation=r) for i, r in (("new", "UN R148"), ("old", "UN R7"))]
    decisions = [
        ApplicabilityDecision(regulation=r, version_label="v", document_ids=(i,),
                              status=ApplicabilityStatus.APPLICABLE, explanation="scope fits")
        for i, r in (("new", "UN R148"), ("old", "UN R7"))
    ]
    ctx = RunContext(run_input=run_input, corpus=Corpus(), llm=None)
    updated, selected = apply_consolidation(ctx, decisions, docs)

    by_reg = {d.regulation: d for d in updated}
    assert by_reg["UN R7"].status is ApplicabilityStatus.EXCLUDED
    assert "combines the provisions of UN R7" in by_reg["UN R7"].explanation
    assert by_reg["UN R148"].status is ApplicabilityStatus.APPLICABLE
    assert [d.regulation for d in selected] == ["UN R148"]
