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

    import dataclasses

    model_only = dataclasses.replace(
        run_input, config=dataclasses.replace(run_input.config, extraction_mode="model")
    )
    with pytest.raises(LLMConfigurationError, match="credit balance"):
        run_extraction(model_only, corpus, UnusableAdapter(script=script))


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


def test_sections_are_ordered_naturally_when_the_corpus_gives_no_ordinal():
    """A plain string sort puts 3.10 before 3.2, silently reordering a document.

    The real corpus reports ordinal=0 for every section, so this fallback is
    what actually determines reading order today.
    """
    from certivity.agent.plan import _natural_label_key

    labels = ["3.10", "3.2", "3.1", "10.1", "2", "3.2.1"]
    assert sorted(labels, key=_natural_label_key) == [
        "2", "3.1", "3.2", "3.2.1", "3.10", "10.1",
    ]


# --------------------------------------------------------------------------- #
# Inspection priority
#
# The first real end-to-end run spent its entire model budget on UN R10's
# definitions clause and produced four requirements that appear nowhere in the
# dev case's answer key, while R148's photometric sections went unread. Order
# decides what a budget-limited run actually contains.
# --------------------------------------------------------------------------- #


def test_priority_prefers_obligations_and_product_terms_over_admin():
    from certivity.agent.plan import _profile_terms, section_priority
    from certivity.contracts import ProductFact, ProductProfile, Section, SourcePassage

    profile = ProductProfile(
        product_type="rear signalling lamp",
        facts=(ProductFact(key="functions", value="rear position, stop", basis="stated"),),
    )
    terms = _profile_terms(profile)

    def section(heading, label="6.1", namespace="main"):
        return Section(
            section_id="s", document_id="d", parent_section_id=None,
            heading=heading, namespace=namespace, paragraph_label=label,
        )

    def passage(text):
        return [SourcePassage(
            passage_id="p", document_id="d", section_id="s", text=text,
            paragraph_label="6.1", heading_ancestry=(), printed_start_page="1",
        )]

    substantive = section_priority(
        section("Rear position lamp"),
        passage("The rear position lamp shall have a luminous intensity of 4 cd and "
                "shall not exceed 17 cd."),
        terms,
    )
    admin = section_priority(
        section("Names and addresses of Technical Services", "14"),
        passage("The Contracting Parties shall communicate the names and addresses "
                "of the Technical Services."),
        terms,
    )
    empty = section_priority(section("Heading only", "7"), [], terms)

    assert substantive > admin, "a requirement-bearing section must outrank admin text"
    # Admin text can still carry a citable requirement - the dev answer key
    # includes marking and approval provisions - so it must outrank a section
    # with no readable text at all, which can only waste a call.
    assert admin > empty, "an unreadable section is the least useful thing to read"


def test_priority_reorders_the_plan_without_changing_coverage(
    run_input, corpus, script
):
    """Order changes; the ledger does not. Every planned section still appears,
    so a completed run is identical either way."""
    from certivity.agent.plan import build_inspection_plan
    from certivity.agent.context import RunContext
    from certivity.contracts import ProductProfile

    ctx = RunContext(run_input=run_input, corpus=corpus, llm=None)
    documents = corpus.list_documents()
    plan_unranked, cov_a = build_inspection_plan(ctx, documents, None)
    plan_ranked, cov_b = build_inspection_plan(
        ctx, documents, ProductProfile(product_type="signalling device")
    )

    assert {i.section_id for i in plan_unranked} == {i.section_id for i in plan_ranked}
    assert len(cov_a) == len(cov_b)


# --------------------------------------------------------------------------- #
# Bounded parallel extraction
#
# Required, not an optimisation: ~1,201 extraction calls at ~20 s each is 6.7
# hours serially against a 5-hour submission window.
# --------------------------------------------------------------------------- #


def _run_with_workers(run_input, corpus, script, workers):
    from certivity.contracts import RunConfig, RunInput, content_hash
    from tests.fixtures.contracts import ScriptedLLMAdapter

    cfg = RunConfig(
        max_model_calls=60,
        passages_per_batch=run_input.config.passages_per_batch,
        max_parallel_sections=workers,
    )
    scoped = RunInput(
        run_id=f"run-workers-{workers}",
        team_id=run_input.team_id,
        case_id=run_input.case_id,
        corpus_snapshot_id=run_input.corpus_snapshot_id,
        input_hash=content_hash("workers", workers),
        specification_text=run_input.specification_text,
        config=cfg,
    )
    return run_extraction(scoped, corpus, ScriptedLLMAdapter(script=script))


def test_parallel_extraction_matches_serial_exactly(run_input, corpus, script):
    """Concurrency must not change the result, only the wall clock.

    Compared on draft ids, text, citations and coverage. Results are keyed by
    section and applied in plan order, so completion order cannot leak into the
    output.
    """
    serial = _run_with_workers(run_input, corpus, script, 1)
    parallel = _run_with_workers(run_input, corpus, script, 4)

    def shape(draft):
        return [
            (d.draft_id, d.text, d.primary_passage_id, tuple(d.supporting_passage_ids))
            for d in draft.draft_requirements
        ]

    assert shape(serial) == shape(parallel)
    assert serial.coverage_counts() == parallel.coverage_counts()
    assert {c.target_id: c.status for c in serial.coverage} == {
        c.target_id: c.status for c in parallel.coverage
    }
    assert serial.partial == parallel.partial


def test_parallel_extraction_still_honours_the_model_budget(run_input, corpus, script):
    """A budget hit mid-flight must degrade coverage, not raise or hang."""
    from certivity.contracts import CoverageStatus, RunConfig, RunInput, TargetKind, content_hash
    from tests.fixtures.contracts import ScriptedLLMAdapter

    scoped = RunInput(
        run_id="run-parallel-budget",
        team_id=run_input.team_id,
        case_id=run_input.case_id,
        corpus_snapshot_id=run_input.corpus_snapshot_id,
        input_hash=content_hash("parallel-budget"),
        specification_text=run_input.specification_text,
        config=RunConfig(max_model_calls=5, max_parallel_sections=4),
    )
    draft = run_extraction(scoped, corpus, ScriptedLLMAdapter(script=script, max_calls=5))

    assert draft.partial is True
    assert any("budget" in w for w in draft.warnings), draft.warnings
    pending = [
        c for c in draft.coverage
        if c.target_kind is TargetKind.SECTION and c.status is CoverageStatus.PENDING
    ]
    assert pending, "unread sections must stay visible as pending"


def test_usage_totals_survive_concurrent_accumulation(run_input, corpus, script):
    """Usage is merged from several threads; a lost write would under-report spend."""
    from tests.fixtures.contracts import ScriptedLLMAdapter

    llm = ScriptedLLMAdapter(script=script)
    from certivity.contracts import RunConfig, RunInput, content_hash

    scoped = RunInput(
        run_id="run-usage", team_id=run_input.team_id, case_id=run_input.case_id,
        corpus_snapshot_id=run_input.corpus_snapshot_id,
        input_hash=content_hash("usage"), specification_text=run_input.specification_text,
        config=RunConfig(max_model_calls=60, max_parallel_sections=4),
    )
    draft = run_extraction(scoped, corpus, llm)
    assert draft.usage.calls == len(llm.calls)


def test_emit_event_is_never_called_while_holding_the_context_lock():
    """A lock may guard this object's own state and nothing else.

    emit_event is the caller's code and writes to the application's run store,
    which has its own lock. Holding RunContext.lock across that call inverted
    the lock order against a worker that held the store's lock and then called
    warn(). The first full run deadlocked after ~45 minutes: process alive,
    sleeping, 25 threads, no progress.
    """
    from certivity.agent.context import RunContext

    observed: list[bool] = []

    def emit(event):
        # If the lock were held by this thread's caller, a non-blocking
        # acquire from here would fail.
        got = ctx.lock.acquire(blocking=False)
        observed.append(got)
        if got:
            ctx.lock.release()

    ctx = RunContext(run_input=None, corpus=None, llm=None, emit_event=emit)
    ctx.event("extract", "progress")
    ctx.warn("something to report", stage="extract")

    assert observed, "emit_event was never called"
    assert all(observed), (
        "emit_event was called while RunContext.lock was held; that is the "
        "lock-order inversion that deadlocked the first full run"
    )
