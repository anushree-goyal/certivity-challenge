"""End-to-end checks for the extraction component against the synthetic corpus.

These verify the behaviour the brief asks for: a real section produces
source-backed obligations, a reference is either inspected or explicitly
unresolved, a conditional exception survives, coverage is honest, and changing
the case input changes the result without any source-code edit.
"""

from __future__ import annotations

import json

import pytest

from certivity.agent import run_extraction
from certivity.contracts import (
    ApplicabilityStatus,
    CoverageStatus,
    ObligationKind,
    PipelineDraft,
    ReferenceStatus,
    RunConfig,
    RunInput,
    TargetKind,
    content_hash,
    from_jsonable,
    to_jsonable,
)
from tests.fixtures.contracts import corpus as syn


def test_run_produces_source_backed_obligations(run_input, corpus, llm):
    draft = run_extraction(run_input, corpus, llm)

    assert draft.draft_requirements, "no obligations were extracted"
    known_passages = {p.passage_id for p in syn.PASSAGES}
    for item in draft.draft_requirements:
        # Every citation must exist in the corpus, and the primary citation must
        # be among the draft's own supporting evidence.
        assert item.primary_passage_id in known_passages
        assert item.primary_passage_id in item.supporting_passage_ids
        assert set(item.supporting_passage_ids) <= known_passages
        assert item.text.strip()


def test_applicability_is_decided_from_scope_text_not_titles(run_input, corpus, llm):
    draft = run_extraction(run_input, corpus, llm)
    by_regulation = {d.regulation: d for d in draft.applicability_decisions}

    assert set(by_regulation) == {syn.REG_A, syn.REG_B}, "every indexed regulation needs a decision"
    assert by_regulation[syn.REG_A].status is ApplicabilityStatus.APPLICABLE
    # The decision cites the scope passage it rests on.
    assert syn.A_SCOPE_P1.passage_id in by_regulation[syn.REG_A].scope_evidence
    assert by_regulation[syn.REG_A].conditions


def test_conditional_exception_and_range_survive_extraction(run_input, corpus, llm):
    draft = run_extraction(run_input, corpus, llm)
    intensity = [
        item
        for item in draft.draft_requirements
        if item.primary_passage_id == syn.A_SPEC_INTENSITY.passage_id
    ]
    assert len(intensity) == 1, "the range must stay one obligation, not be split"
    item = intensity[0]

    assert item.exceptions, "the R2 exception was dropped"
    assert "R2" in " ".join(item.exceptions)
    assert "4" in item.text and "12" in item.text, "the range lost its bounds"
    assert item.applicability_condition


def test_definition_carrying_a_measurable_rule_is_extracted(run_input, corpus, llm):
    draft = run_extraction(run_input, corpus, llm)
    definition_rules = [
        item
        for item in draft.draft_requirements
        if item.obligation_kind is ObligationKind.DEFINITION_RULE
    ]
    # Definitions are not blanket-excluded: 2.2 imposes a 1,5 degree limit.
    assert definition_rules
    assert "1,5" in definition_rules[0].text, "the decimal separator was altered"


def test_reference_is_followed_and_unresolvable_one_is_recorded(run_input, corpus, llm):
    draft = run_extraction(run_input, corpus, llm)

    # The resolvable annex reference was inspected: an obligation cites the
    # annex passage that only became available after following the reference.
    cited = {
        pid for item in draft.draft_requirements for pid in item.supporting_passage_ids
    }
    assert syn.A_ANNEX_P1.passage_id in cited, "the resolved reference was never inspected"

    # The reference outside the corpus is reported, not silently ignored.
    statuses = {r.status for r in draft.unresolved_references}
    assert ReferenceStatus.OUTSIDE_CORPUS in statuses
    outside = [
        r for r in draft.unresolved_references if r.status is ReferenceStatus.OUTSIDE_CORPUS
    ]
    assert "REG Z" in outside[0].request.raw_reference

    # And it is attached to the draft whose own evidence raised it.
    carriers = [
        item for item in draft.draft_requirements if item.unresolved_references
    ]
    assert carriers
    for item in carriers:
        for resolution in item.unresolved_references:
            assert resolution.request.originating_passage_id in item.supporting_passage_ids


def test_coverage_is_recorded_for_every_planned_section(run_input, corpus, llm):
    draft = run_extraction(run_input, corpus, llm)
    sections = {
        item.target_id: item
        for item in draft.coverage
        if item.target_kind is TargetKind.SECTION
    }

    # Every section of the selected document appears, including the excluded one.
    assert syn.A_SCOPE in sections and syn.A_SPECS in sections
    assert sections[syn.A_CONTENTS].status is CoverageStatus.EXCLUDED
    assert sections[syn.A_CONTENTS].explanation, "an exclusion must carry its reason"

    # The excluded regulation's sections were never planned.
    assert syn.B_SCOPE not in sections

    processed = [s for s in sections.values() if s.status is CoverageStatus.PROCESSED]
    assert processed
    for item in processed:
        assert item.inspected_passage_ids, "a processed section must record what it read"


def test_full_coverage_is_not_partial_but_a_capped_run_is(run_input, corpus, llm, script):
    full = run_extraction(run_input, corpus, llm)
    assert full.partial is False, f"unexpected partial run: {full.warnings}"

    capped_input = RunInput(
        run_id="run-test-capped",
        team_id=run_input.team_id,
        case_id=run_input.case_id,
        corpus_snapshot_id=run_input.corpus_snapshot_id,
        input_hash=content_hash("capped"),
        specification_text=run_input.specification_text,
        config=RunConfig(max_sections_per_document=1, max_model_calls=60),
    )
    from tests.fixtures.contracts import ScriptedLLMAdapter

    capped = run_extraction(capped_input, corpus, ScriptedLLMAdapter(script=script))
    assert capped.partial is True
    assert any("max_sections_per_document" in w for w in capped.warnings)


def test_exhausted_model_budget_degrades_coverage_instead_of_raising(
    run_input, corpus, script
):
    from tests.fixtures.contracts import ScriptedLLMAdapter

    # Enough calls for the profile and applicability, not enough to finish reading.
    starved = ScriptedLLMAdapter(script=script, max_calls=4)
    draft = run_extraction(run_input, corpus, starved)

    assert draft.partial is True
    assert any("budget" in w for w in draft.warnings), draft.warnings
    pending = [
        item
        for item in draft.coverage
        if item.target_kind is TargetKind.SECTION
        and item.status is CoverageStatus.PENDING
    ]
    assert pending, "unread sections must remain visible as pending"


def test_draft_is_json_serialisable_for_the_api_boundary(run_input, corpus, llm):
    draft = run_extraction(run_input, corpus, llm)
    restored = from_jsonable(PipelineDraft, json.loads(json.dumps(to_jsonable(draft))))
    assert restored == draft


def test_ids_are_stable_across_identical_runs(run_input, corpus, script):
    from tests.fixtures.contracts import ScriptedLLMAdapter

    first = run_extraction(run_input, corpus, ScriptedLLMAdapter(script=script))
    second = run_extraction(run_input, corpus, ScriptedLLMAdapter(script=script))
    assert [d.draft_id for d in first.draft_requirements] == [
        d.draft_id for d in second.draft_requirements
    ]


def test_usage_and_prompt_versions_are_recorded(run_input, corpus, llm):
    draft = run_extraction(run_input, corpus, llm)
    assert draft.usage.calls == len(llm.calls)
    assert draft.prompt_versions["extract_obligations"]
    assert draft.corpus_snapshot_id == syn.SNAPSHOT_ID


def test_an_unusable_model_aborts_the_run_instead_of_returning_an_empty_draft(
    run_input, corpus, script
):
    """A missing credential or an empty credit balance is not recoverable.

    Returning a draft with zero obligations would look like a completed run that
    simply found nothing, which is a materially different and false claim.
    """
    from certivity.contracts import LLMConfigurationError
    from tests.fixtures.contracts import ScriptedLLMAdapter

    class UnusableAdapter(ScriptedLLMAdapter):
        def generate_json(self, *args, **kwargs):
            raise LLMConfigurationError(
                "the model is not usable with this account's configuration: "
                "Your credit balance is too low"
            )

    with pytest.raises(LLMConfigurationError, match="credit balance"):
        run_extraction(run_input, corpus, UnusableAdapter(script=script))


def test_references_the_corpus_detects_are_followed_even_if_unreported(
    run_input, corpus, script
):
    """Reference recall must not depend on the model noticing a citation.

    The script here reports no references at all. The corpus detects them, so
    the annex target must still be inspected.
    """
    from tests.fixtures.contracts import ScriptedLLMAdapter

    original = script["extract_obligations"]

    def silent(payload):
        data = original(payload)
        data["references"] = []
        return data

    script["extract_obligations"] = silent
    draft = run_extraction(run_input, corpus, ScriptedLLMAdapter(script=script))

    statuses = {r.status for r in draft.unresolved_references}
    assert ReferenceStatus.OUTSIDE_CORPUS in statuses, (
        "the corpus-detected reference was never followed"
    )
    assert all(r.request is not None for r in draft.unresolved_references), (
        "every resolution must carry the request it answers"
    )
