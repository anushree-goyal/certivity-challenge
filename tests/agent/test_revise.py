"""Revision from review findings.

Only drafts a finding names may change, and a finding must never be closed by
inventing a citation. "The finding is mistaken" and "still unresolved" are both
legitimate outcomes.
"""

from __future__ import annotations

from typing import Any

import pytest

from certivity.agent import revise_extraction, run_extraction
from certivity.agent.prompts import PROMPT_VERSIONS
from certivity.contracts import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    TargetKind,
)
from tests.fixtures.contracts import ScriptedLLMAdapter, default_script
from tests.fixtures.contracts import corpus as syn


@pytest.fixture
def extracted(run_input, corpus, script):
    llm = ScriptedLLMAdapter(script=script)
    return run_extraction(run_input, corpus, llm)


def finding_for(draft_id: str, **kwargs: Any) -> ReviewFinding:
    return ReviewFinding(
        finding_id=f"fnd-{draft_id}",
        target_kind=TargetKind.DRAFT,
        target_id=draft_id,
        category=kwargs.pop("category", FindingCategory.MISSING_CONDITION),
        explanation=kwargs.pop("explanation", "the condition may be missing"),
        severity=kwargs.pop("severity", FindingSeverity.REVISION_REQUESTED),
        **kwargs,
    )


def test_a_mistaken_finding_leaves_the_draft_unchanged(
    run_input, corpus, script, extracted
):
    target = extracted.draft_requirements[0]
    revised = revise_extraction(
        run_input,
        extracted,
        [finding_for(target.draft_id)],
        corpus,
        ScriptedLLMAdapter(script=script),
    )

    assert revised.draft_by_id(target.draft_id) == target
    assert len(revised.draft_requirements) == len(extracted.draft_requirements)


def test_withdrawal_removes_the_draft_and_its_coverage_link(
    run_input, corpus, script, extracted
):
    target = extracted.draft_requirements[0]
    script["revise_obligations"] = lambda payload: {
        "decisions": [
            {
                "draft_id": item["draft_id"],
                "action": "withdraw" if item["draft_id"] == target.draft_id else "keep",
                "explanation": "the cited passage does not state this obligation",
                "obligation": None,
            }
            for item in payload["drafts"]
        ]
    }
    revised = revise_extraction(
        run_input, extracted, [finding_for(target.draft_id)], corpus,
        ScriptedLLMAdapter(script=script),
    )

    assert revised.draft_by_id(target.draft_id) is None
    assert len(revised.draft_requirements) == len(extracted.draft_requirements) - 1
    # Coverage must not keep pointing at a draft that no longer exists.
    linked = {d for item in revised.coverage for d in item.draft_ids}
    assert target.draft_id not in linked


def test_a_correction_replaces_the_draft_and_records_its_ancestry(
    run_input, corpus, script, extracted
):
    target = next(
        item
        for item in extracted.draft_requirements
        if item.primary_passage_id == syn.A_SPEC_COLOUR.passage_id
    )

    def revise(payload: Any) -> dict[str, Any]:
        decisions = []
        for item in payload["drafts"]:
            if item["draft_id"] != target.draft_id:
                decisions.append(
                    {
                        "draft_id": item["draft_id"],
                        "action": "keep",
                        "explanation": "correct as written",
                        "obligation": None,
                    }
                )
                continue
            decisions.append(
                {
                    "draft_id": item["draft_id"],
                    "action": "revise",
                    "explanation": "the scope condition was missing",
                    "obligation": {
                        "text": (
                            "For a category SX signalling device on an M1 vehicle, the "
                            "light emitted shall be red."
                        ),
                        "supporting_passage_ids": [item["primary_passage_id"]],
                        "primary_passage_id": item["primary_passage_id"],
                        "obligation_kind": "mandatory",
                        "product_applicability": "applies",
                        "applicability_condition": (
                            "device is a category SX signalling device on an M1 vehicle"
                        ),
                        "applicability_reason": "The product is such a device.",
                        "exceptions": [],
                        "values": [
                            {
                                "quantity": "colour",
                                "value": "red",
                                "unit": None,
                                "comparator": "=",
                            }
                        ],
                        "notes": None,
                    },
                }
            )
        return {"decisions": decisions}

    script["revise_obligations"] = revise
    revised = revise_extraction(
        run_input, extracted, [finding_for(target.draft_id)], corpus,
        ScriptedLLMAdapter(script=script),
    )

    replacement = next(
        item for item in revised.draft_requirements if item.revision_of == target.draft_id
    )
    assert "category SX" in replacement.text
    assert replacement.applicability_condition
    assert replacement.draft_id != target.draft_id
    assert revised.draft_by_id(target.draft_id) is None
    # Reference the source of truth rather than a literal, so a prompt-version
    # bump does not fail a test that is about revision ancestry.
    assert replacement.prompt_version == PROMPT_VERSIONS["revise_obligations"]


def test_a_correction_citing_an_unsupplied_passage_is_refused(
    run_input, corpus, script, extracted
):
    target = extracted.draft_requirements[0]
    script["revise_obligations"] = lambda payload: {
        "decisions": [
            {
                "draft_id": target.draft_id,
                "action": "revise",
                "explanation": "corrected",
                "obligation": {
                    "text": "An obligation with an invented citation.",
                    "supporting_passage_ids": ["invented#main/1@001"],
                    "primary_passage_id": "invented#main/1@001",
                    "obligation_kind": "mandatory",
                    "product_applicability": "applies",
                    "applicability_condition": None,
                    "applicability_reason": None,
                    "exceptions": [],
                    "values": [],
                    "notes": None,
                },
            }
        ]
    }
    revised = revise_extraction(
        run_input, extracted, [finding_for(target.draft_id)], corpus,
        ScriptedLLMAdapter(script=script),
    )

    assert revised.draft_by_id(target.draft_id) == target, "the original must stand"
    assert any("not citable" in w for w in revised.warnings)


def test_an_unresolved_finding_is_reported_not_silently_closed(
    run_input, corpus, script, extracted
):
    target = extracted.draft_requirements[0]
    script["revise_obligations"] = lambda payload: {
        "decisions": [
            {
                "draft_id": target.draft_id,
                "action": "unresolved",
                "explanation": "Annex 3 would be needed and is not in the snapshot.",
                "obligation": None,
            }
        ]
    }
    revised = revise_extraction(
        run_input, extracted, [finding_for(target.draft_id)], corpus,
        ScriptedLLMAdapter(script=script),
    )

    assert revised.draft_by_id(target.draft_id) == target
    assert any("Annex 3" in w for w in revised.warnings)


def test_a_finding_for_an_unknown_draft_is_ignored_with_a_warning(
    run_input, corpus, script, extracted
):
    revised = revise_extraction(
        run_input, extracted, [finding_for("drf-does-not-exist")], corpus,
        ScriptedLLMAdapter(script=script),
    )
    assert revised.draft_requirements == extracted.draft_requirements
    assert any("unknown draft" in w for w in revised.warnings)


def test_requested_section_context_is_loaded_for_the_revision(
    run_input, corpus, script, extracted
):
    """A reviewer asking for a section's context must actually get it."""
    target = next(
        item
        for item in extracted.draft_requirements
        if item.section_id == syn.A_DEFS
    )
    seen: list[list[str]] = []

    def revise(payload: Any) -> dict[str, Any]:
        seen.append([p["passage_id"] for p in payload["source_passages"]])
        return {
            "decisions": [
                {
                    "draft_id": item["draft_id"],
                    "action": "keep",
                    "explanation": "unchanged",
                    "obligation": None,
                }
                for item in payload["drafts"]
            ]
        }

    script["revise_obligations"] = revise
    revise_extraction(
        run_input,
        extracted,
        [finding_for(target.draft_id, requested_context=(syn.A_SCOPE,))],
        corpus,
        ScriptedLLMAdapter(script=script),
    )

    assert seen, "the revision call was never made"
    assert syn.A_SCOPE_P1.passage_id in seen[0]


def test_usage_accumulates_across_extraction_and_revision(
    run_input, corpus, script, extracted
):
    target = extracted.draft_requirements[0]
    llm = ScriptedLLMAdapter(script=script)
    revised = revise_extraction(
        run_input, extracted, [finding_for(target.draft_id)], corpus, llm
    )
    assert revised.usage.calls == extracted.usage.calls + len(llm.calls)


def test_informational_findings_requesting_nothing_do_not_trigger_revision(
    run_input, corpus, script, extracted
):
    """A heuristic flag, a duplicate note or an unreviewed draft is a record,
    not a request. Revising on it would spend model calls to change nothing.

    This is the trigger rule the API layer also reads, via
    ReviewFinding.is_actionable, so the two cannot disagree.
    """
    from certivity.contracts import FindingSeverity

    target = extracted.draft_requirements[0]
    informational = finding_for(
        target.draft_id,
        category=FindingCategory.DUPLICATE,
        explanation="possible duplicate, not resolved",
        severity=FindingSeverity.INFORMATIONAL,
    )
    assert informational.is_actionable is False

    llm = ScriptedLLMAdapter(script=script)
    revised = revise_extraction(run_input, extracted, [informational], corpus, llm)

    assert revised.draft_requirements == extracted.draft_requirements
    assert llm.calls == [], "no model call should be made for a record-only finding"
    assert any("informational finding" in w for w in revised.warnings)


def test_an_informational_finding_that_requests_context_is_still_acted_on(
    run_input, corpus, script, extracted
):
    """Severity alone does not decide: an explicit request does."""
    from certivity.contracts import FindingSeverity

    target = extracted.draft_requirements[0]
    requesting = finding_for(
        target.draft_id,
        category=FindingCategory.CONTEXT_REQUESTED,
        explanation="needs the scope condition",
        severity=FindingSeverity.INFORMATIONAL,
        requested_context=(syn.A_SCOPE,),
    )
    assert requesting.is_actionable is True

    llm = ScriptedLLMAdapter(script=script)
    revise_extraction(run_input, extracted, [requesting], corpus, llm)
    assert llm.calls, "a requested context must reach extraction"
