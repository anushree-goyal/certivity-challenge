"""One real model call against the synthetic corpus. Skipped by default.

Every other test in this directory uses the scripted fixture: those verify the
component's *code paths*. This file is the only check on whether the **prompts
actually work**, which no fixture can tell us.

It is skipped unless both conditions hold, so it can never surprise anyone with
a bill:

* ``CERTIVITY_RUN_REAL_MODEL_TESTS=1`` is set, and
* a provider credential is resolvable.

Run it with::

    CERTIVITY_RUN_REAL_MODEL_TESTS=1 .venv/bin/python -m pytest \\
        tests/agent/test_real_model.py -v -s

Cost is bounded by ``RunConfig.max_model_calls`` below. These assertions are
deliberately loose: they check that the prompts produce *usable, honest,
correctly-cited* output, not that they produce one particular answer. A real
model is not deterministic, and a test that demanded an exact string would
fail for the wrong reason.
"""

from __future__ import annotations

import json
import os

import pytest

from certivity.agent import run_extraction
from certivity.agent.prompts import (
    PRODUCT_PROFILE_INSTRUCTIONS,
    PRODUCT_PROFILE_SCHEMA,
    PROMPT_VERSIONS,
)
from certivity.contracts import (
    ApplicabilityStatus,
    LLMCallError,
    LLMConfigurationError,
    CoverageStatus,
    RunConfig,
    TargetKind,
    make_run_input,
)
from certivity.llm import create_llm
from tests.fixtures.contracts import InMemoryCorpusReader
from tests.fixtures.contracts import corpus as syn

ENABLED = os.environ.get("CERTIVITY_RUN_REAL_MODEL_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not ENABLED,
    reason="set CERTIVITY_RUN_REAL_MODEL_TESTS=1 to make real, billed model calls",
)

SPEC = (
    "Rear signalling device for a passenger car of category M1. The housing "
    "carries a polycarbonate lens and the device emits red light to signal the "
    "presence of the vehicle to other road users. The luminous intensity and "
    "any category marking are not specified in this document."
)


@pytest.fixture(scope="module")
def live_llm():
    """A usable adapter, or a single clear explanation of why there is none.

    A credential being *present* does not make the model usable: the key can be
    valid while the account has no credit. That returns HTTP 400, not 401, so it
    is only detectable by making a call. Preflighting it once here means the
    whole file reports the real cause once, instead of three downstream
    assertions each misdiagnosing it as a prompt-quality failure.
    """
    adapter = create_llm(max_calls=25)
    if not adapter.credentials_available():
        pytest.skip(
            "no provider credential is resolvable; set ANTHROPIC_API_KEY, "
            "ANTHROPIC_AUTH_TOKEN, or run 'ant auth login'"
        )

    tiny_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
    }
    try:
        adapter.generate_json(
            "preflight",
            "Reply with {\"ok\": true} and nothing else.",
            {},
            tiny_schema,
            max_output_tokens=64,
        )
    except LLMConfigurationError as exc:
        # Not a skip: the credential is there, so this is a real blocker the
        # operator has to act on, and a silent skip would hide it.
        pytest.fail(
            "the credential works but the model is not usable - no prompt was "
            f"tested. Provider said: {exc}",
            pytrace=False,
        )
    except LLMCallError as exc:
        pytest.fail(f"the preflight call failed: {exc}", pytrace=False)

    print(f"\n--- preflight ok: {adapter.describe()['model']}, "
          f"{adapter.describe()['credential_source']} ---")
    return adapter


def test_one_real_structured_call_returns_schema_valid_output(live_llm):
    """The narrowest possible check: one call, one schema."""
    response = live_llm.generate_json(
        "product_profile",
        PRODUCT_PROFILE_INSTRUCTIONS,
        {"case_id": "real-smoke-test", "specification_text": SPEC, "drawings": []},
        PRODUCT_PROFILE_SCHEMA,
        prompt_version=PROMPT_VERSIONS["product_profile"],
    )

    print("\n--- product profile ---")
    print(json.dumps(response.data, indent=1, ensure_ascii=False)[:2000])
    print(f"--- {response.model}, {response.usage.input_tokens} in / "
          f"{response.usage.output_tokens} out, {response.latency_ms:.0f} ms ---")

    assert response.data["product_type"], "the model established no product type"
    # The unknowns list is what keeps regulations in scope later, so a prompt
    # that produces an empty one has failed at its most important job.
    assert response.data["unknowns"], (
        "the profile recorded no unknowns, although the specification explicitly "
        "leaves luminous intensity and marking unstated"
    )
    assert response.usage.calls == 1


def test_a_real_run_produces_honest_source_backed_output(live_llm):
    """A full run over the synthetic corpus, with a real model.

    The corpus is synthetic, so this says nothing about real regulation text.
    What it does test is that the prompts, schemas and guards survive contact
    with a real model rather than only with a scripted one.
    """
    run_input = make_run_input(
        run_id="run-real-smoke",
        team_id="team-anushree",
        case_id="synthetic-signalling-device",
        corpus_snapshot_id=syn.SNAPSHOT_ID,
        specification_text=SPEC,
        config=RunConfig(max_model_calls=25, passages_per_batch=4),
    )
    draft = run_extraction(run_input, InMemoryCorpusReader(), live_llm)

    print(f"\n--- {len(draft.draft_requirements)} obligation(s), "
          f"coverage {draft.coverage_counts()}, partial={draft.partial} ---")
    for item in draft.draft_requirements:
        print(f"\n[{item.obligation_kind.value}] {item.text}")
        print(f"  primary: {item.primary_passage_id}")
        if item.applicability_condition:
            print(f"  condition: {item.applicability_condition}")
        if item.exceptions:
            print(f"  exceptions: {list(item.exceptions)}")
    if draft.warnings:
        print("\n--- warnings ---")
        for warning in draft.warnings:
            print(f"  {warning}")

    # Every citation must be real. This is the assertion that matters most: a
    # real model is the only thing that can fabricate a passage id, and the
    # extractor is supposed to drop one when it does.
    known = {p.passage_id for p in syn.PASSAGES}
    for item in draft.draft_requirements:
        assert item.primary_passage_id in known, "a fabricated citation survived"
        assert set(item.supporting_passage_ids) <= known
        assert item.primary_passage_id in item.supporting_passage_ids

    assert draft.draft_requirements, "a real model extracted nothing at all"

    # No two drafts may state the same obligation. A live run produced each of
    # two obligations twice - once from the section that owns the passage and
    # once from a section that received it as context or as a followed
    # reference - which would put the same requirement in the submission twice
    # under two different citations.
    normalised = [
        " ".join(item.text.lower().split()) for item in draft.draft_requirements
    ]
    repeats = {t for t in normalised if normalised.count(t) > 1}
    assert not repeats, f"the same obligation was extracted more than once: {repeats}"

    # Scope statements are applicability, not requirements.
    scope_cited = [
        item
        for item in draft.draft_requirements
        if item.primary_passage_id == syn.A_SCOPE_P1.passage_id
    ]
    assert not scope_cited, (
        "the regulation's scope statement was returned as an obligation: "
        f"{[i.text for i in scope_cited]}"
    )

    # Every indexed regulation must be decided, and the irrelevant one must not
    # be reported as applicable.
    decisions = {d.regulation: d for d in draft.applicability_decisions}
    assert set(decisions) == {syn.REG_A, syn.REG_B}
    assert decisions[syn.REG_A].status is not ApplicabilityStatus.EXCLUDED, (
        "the regulation whose scope text matches the product was excluded"
    )
    assert decisions[syn.REG_B].status is not ApplicabilityStatus.APPLICABLE, (
        "a regulation about pressure vessels was held applicable to a lamp"
    )

    # Coverage must be a real ledger, not a claim.
    processed = [
        item
        for item in draft.coverage
        if item.target_kind is TargetKind.SECTION
        and item.status is CoverageStatus.PROCESSED
    ]
    assert processed
    for item in processed:
        assert item.inspected_passage_ids


def test_the_intensity_exception_survives_a_real_model(live_llm):
    """The single most failure-prone extraction in the fixture corpus.

    Paragraph 3.2 states a range *and* a different range for marked devices.
    Losing the exception, or splitting the range into two obligations, are the
    two classic ways to get this wrong.
    """
    run_input = make_run_input(
        run_id="run-real-exception",
        team_id="team-anushree",
        case_id="synthetic-signalling-device",
        corpus_snapshot_id=syn.SNAPSHOT_ID,
        specification_text=SPEC,
        config=RunConfig(max_model_calls=25, passages_per_batch=4),
    )
    draft = run_extraction(run_input, InMemoryCorpusReader(), live_llm)

    from_intensity = [
        item
        for item in draft.draft_requirements
        if item.primary_passage_id == syn.A_SPEC_INTENSITY.passage_id
    ]
    print("\n--- obligations cited to paragraph 3.2 ---")
    for item in from_intensity:
        print(f"  {item.text}")
        print(f"    exceptions: {list(item.exceptions)}")

    assert from_intensity, "paragraph 3.2 produced no obligation"
    # The R2 carve-out must appear somewhere in the obligations drawn from 3.2 -
    # as an exception field or inside the text. Where it lands is a judgement
    # call; losing it is not.
    carved = " ".join(
        item.text + " " + " ".join(item.exceptions) for item in from_intensity
    )
    assert "R2" in carved, "the R2 exception was lost"
    assert "2" in carved and "6" in carved, "the R2 bounds were lost"
