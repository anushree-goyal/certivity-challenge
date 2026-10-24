"""Guards that hold when the model misbehaves.

The prompts ask for correct behaviour; these tests verify the code does not
depend on the model complying. A fabricated passage id, a fact over-claimed
from a drawing, or an exclusion with no evidence must all be caught here.
"""

from __future__ import annotations

from typing import Any

import pytest

from certivity.agent import run_extraction
from certivity.contracts import ApplicabilityStatus, CoverageStatus, TargetKind
from tests.fixtures.contracts import ScriptedLLMAdapter, default_script
from tests.fixtures.contracts import corpus as syn


def run_with(script: dict[str, Any], run_input, corpus, **kwargs):
    llm = ScriptedLLMAdapter(script=script, **kwargs)
    return run_extraction(run_input, corpus, llm), llm


# --------------------------------------------------------------------------- #
# Citation integrity
# --------------------------------------------------------------------------- #


def test_fabricated_primary_citation_is_dropped(run_input, corpus, script):
    """A source id that was never supplied is worse than a missing requirement:
    it looks checkable and is not."""
    script["extract_obligations"] = lambda payload: {
        "obligations": [
            {
                "text": "The device shall be entirely invented.",
                "supporting_passage_ids": ["made-up-doc#main/9@001"],
                "primary_passage_id": "made-up-doc#main/9@001",
                "obligation_kind": "mandatory",
                "applicability_condition": None,
                "applicability_reason": None,
                "exceptions": [],
                "values": [],
                "notes": None,
            }
        ],
        "references": [],
        "needs_context": [],
        "section_note": None,
    }
    draft, _ = run_with(script, run_input, corpus)

    assert draft.draft_requirements == ()
    assert any("not supplied to the model" in w for w in draft.warnings)


def test_unsupplied_supporting_ids_are_dropped_but_the_draft_survives(
    run_input, corpus, script
):
    def extract(payload: Any) -> dict[str, Any]:
        primary = payload["primary_passages"]
        if not primary:
            return {"obligations": [], "references": [], "needs_context": [], "section_note": None}
        return {
            "obligations": [
                {
                    "text": "The light emitted shall be red.",
                    "supporting_passage_ids": [
                        primary[0]["passage_id"],
                        "ghost#main/1@001",
                    ],
                    "primary_passage_id": primary[0]["passage_id"],
                    "obligation_kind": "mandatory",
                    "applicability_condition": None,
                    "applicability_reason": None,
                    "exceptions": [],
                    "values": [],
                    "notes": None,
                }
            ],
            "references": [],
            "needs_context": [],
            "section_note": None,
        }

    script["extract_obligations"] = extract
    draft, _ = run_with(script, run_input, corpus)

    assert draft.draft_requirements
    for item in draft.draft_requirements:
        assert "ghost#main/1@001" not in item.supporting_passage_ids
        assert item.primary_passage_id in item.supporting_passage_ids
    assert any("unsupplied supporting passage id" in w for w in draft.warnings)


def test_citing_a_context_passage_as_primary_is_flagged_for_review(
    run_input, corpus, script
):
    """Allowed, because an obligation can rest on an inherited condition - but
    the reviewer must be told the citation is not section-primary."""

    def extract(payload: Any) -> dict[str, Any]:
        context = payload["context_passages"]
        if not context:
            return {"obligations": [], "references": [], "needs_context": [], "section_note": None}
        return {
            "obligations": [
                {
                    "text": "The device shall be a category SX signalling device.",
                    "supporting_passage_ids": [context[0]["passage_id"]],
                    "primary_passage_id": context[0]["passage_id"],
                    "obligation_kind": "mandatory",
                    "applicability_condition": None,
                    "applicability_reason": None,
                    "exceptions": [],
                    "values": [],
                    "notes": None,
                }
            ],
            "references": [],
            "needs_context": [],
            "section_note": None,
        }

    script["extract_obligations"] = extract
    draft, _ = run_with(script, run_input, corpus)

    assert draft.draft_requirements
    item = draft.draft_requirements[0]
    assert item.notes and "context or referenced passage" in item.notes


def test_identical_obligation_repeated_in_one_batch_is_collapsed(
    run_input, corpus, script
):
    def extract(payload: Any) -> dict[str, Any]:
        primary = payload["primary_passages"]
        if not primary:
            return {"obligations": [], "references": [], "needs_context": [], "section_note": None}
        one = {
            "text": "The light emitted shall be red.",
            "supporting_passage_ids": [primary[0]["passage_id"]],
            "primary_passage_id": primary[0]["passage_id"],
            "obligation_kind": "mandatory",
            "applicability_condition": None,
            "applicability_reason": None,
            "exceptions": [],
            "values": [],
            "notes": None,
        }
        return {
            "obligations": [one, dict(one)],
            "references": [],
            "needs_context": [],
            "section_note": None,
        }

    script["extract_obligations"] = extract
    draft, _ = run_with(script, run_input, corpus)
    ids = [item.draft_id for item in draft.draft_requirements]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------- #
# Product facts
# --------------------------------------------------------------------------- #


def test_a_material_claimed_as_seen_in_a_drawing_is_demoted(run_input, corpus, script):
    """A drawing shows geometry. It does not establish a material specification."""
    script["product_profile"] = lambda payload: {
        "product_type": "device",
        "vehicle_categories": [],
        "facts": [],
        "components": [],
        "functions": [],
        "materials": [
            {
                "key": "lens_material",
                "value": "polycarbonate",
                "basis": "observed",
                "evidence": [
                    {"source": "fixture-exploded-view", "quote": None, "location": "lens"}
                ],
                "notes": None,
            }
        ],
        "unknowns": ["marking"],
        "summary": None,
    }
    draft, _ = run_with(script, run_input, corpus)

    material = draft.product_profile.materials[0]
    assert material.basis == "inferred"
    assert material.notes and "cannot establish" in material.notes
    assert any("measured or certified property" in w for w in draft.warnings)


def test_a_fact_with_no_value_becomes_an_unknown(run_input, corpus, script):
    """Otherwise the applicability step loses the signal that keeps a
    regulation under inspection."""
    script["product_profile"] = lambda payload: {
        "product_type": "device",
        "vehicle_categories": [],
        "facts": [
            {
                "key": "luminous_intensity",
                "value": None,
                "basis": "unknown",
                "evidence": [],
                "notes": None,
            }
        ],
        "components": [],
        "functions": [],
        "materials": [],
        "unknowns": [],
        "summary": None,
    }
    draft, _ = run_with(script, run_input, corpus)
    assert "luminous_intensity" in draft.product_profile.unknowns


# --------------------------------------------------------------------------- #
# Applicability
# --------------------------------------------------------------------------- #


def test_exclusion_without_scope_evidence_is_downgraded_to_uncertain(
    run_input, corpus, script
):
    """Exclusion is the only verdict that removes a regulation, so it is the
    one verdict that must cite the passage it rests on."""
    script["document_scope"] = lambda payload: {
        "status": "excluded",
        "explanation": "It just does not seem to apply.",
        "scope_evidence": [],
        "conditions": [],
        "depends_on_unknowns": [],
        "notes": None,
    }
    draft, _ = run_with(script, run_input, corpus)

    statuses = {d.regulation: d.status for d in draft.applicability_decisions}
    assert statuses[syn.REG_A] is ApplicabilityStatus.UNCERTAIN
    assert any("exclusion cited no supplied scope passage" in w for w in draft.warnings)


def test_a_regulation_omitted_from_screening_is_kept_as_a_candidate(
    run_input, corpus, script
):
    """An unmentioned regulation has not been reasoned about; dropping it
    silently is the failure this stage exists to prevent."""
    script["candidate_scope"] = lambda payload: {
        "candidates": [
            {
                "regulation": payload["regulation_index"][0]["regulation"],
                "verdict": "candidate",
                "reason": "matches",
                "depends_on_unknowns": [],
            }
        ]
    }
    draft, _ = run_with(script, run_input, corpus)

    decided = {d.regulation for d in draft.applicability_decisions}
    assert decided == {syn.REG_A, syn.REG_B}
    assert any("not addressed by index screening" in w for w in draft.warnings)


def test_screening_naming_an_unknown_regulation_is_ignored(run_input, corpus, script):
    script["candidate_scope"] = lambda payload: {
        "candidates": [
            {
                "regulation": "UN R999 WHICH IS NOT IN THE INDEX",
                "verdict": "candidate",
                "reason": "hallucinated",
                "depends_on_unknowns": [],
            }
        ]
    }
    draft, _ = run_with(script, run_input, corpus)

    assert all(
        d.regulation in {syn.REG_A, syn.REG_B} for d in draft.applicability_decisions
    )
    assert any("not in the supplied index" in w for w in draft.warnings)


def test_an_uncertain_regulation_is_still_inspected(run_input, corpus, script):
    """Uncertainty about a product fact is not proof that a rule is irrelevant."""
    script["document_scope"] = lambda payload: {
        "status": "uncertain",
        "explanation": "Applies only if the device is category SX, which is unknown.",
        "scope_evidence": [payload["scope_and_definition_passages"][0]["passage_id"]],
        "conditions": ["device is category SX"],
        "depends_on_unknowns": ["device_category"],
        "notes": None,
    }
    draft, _ = run_with(script, run_input, corpus)

    assert draft.draft_requirements, "an uncertain regulation must still be read"
    processed = [
        item
        for item in draft.coverage
        if item.target_kind is TargetKind.SECTION
        and item.status is CoverageStatus.PROCESSED
    ]
    assert processed


def test_scope_decision_citing_an_unsupplied_passage_drops_that_evidence(
    run_input, corpus, script
):
    script["document_scope"] = lambda payload: {
        "status": "applicable",
        "explanation": "In scope.",
        "scope_evidence": [
            payload["scope_and_definition_passages"][0]["passage_id"],
            "invented#main/1@001",
        ],
        "conditions": [],
        "depends_on_unknowns": [],
        "notes": None,
    }
    draft, _ = run_with(script, run_input, corpus)

    decision = next(d for d in draft.applicability_decisions if d.regulation == syn.REG_A)
    assert "invented#main/1@001" not in decision.scope_evidence
    assert decision.scope_evidence
    assert any("not supplied" in w for w in draft.warnings)


# --------------------------------------------------------------------------- #
# Input sensitivity
# --------------------------------------------------------------------------- #


def test_changing_the_case_input_changes_the_profile_without_code_edits(
    run_input, corpus, script
):
    """Nothing about the selection is hardcoded to one case."""
    from certivity.contracts import RunConfig, RunInput, content_hash

    other = RunInput(
        run_id="run-test-other",
        team_id=run_input.team_id,
        case_id="synthetic-pressure-vessel",
        corpus_snapshot_id=run_input.corpus_snapshot_id,
        input_hash=content_hash("other-input"),
        specification_text="A welded pressure vessel for compressed air on an N3 truck.",
        config=RunConfig(max_model_calls=60),
    )

    captured: list[Any] = []

    def product_profile(payload: Any) -> dict[str, Any]:
        captured.append(payload["specification_text"])
        return {
            "product_type": "pressure vessel",
            "vehicle_categories": ["N3"],
            "facts": [],
            "components": [],
            "functions": [],
            "materials": [],
            "unknowns": ["wall_thickness"],
            "summary": None,
        }

    def candidate_scope(payload: Any) -> dict[str, Any]:
        return {
            "candidates": [
                {
                    "regulation": entry["regulation"],
                    "verdict": "candidate"
                    if "pressure" in (entry.get("index_entry") or {}).get("scope_hint", "")
                    else "unlikely",
                    "reason": "matched on the index scope hint",
                    "depends_on_unknowns": [],
                }
                for entry in payload["regulation_index"]
            ]
        }

    script["product_profile"] = product_profile
    script["candidate_scope"] = candidate_scope
    draft, _ = run_with(script, other, corpus)

    assert "pressure vessel" in captured[0]
    assert draft.product_profile.product_type == "pressure vessel"
    # The other regulation is now the candidate, with no source-code change.
    inspected = {
        item.document_id
        for item in draft.coverage
        if item.target_kind is TargetKind.SECTION
    }
    assert syn.DOC_B in inspected
    assert syn.DOC_A not in inspected
