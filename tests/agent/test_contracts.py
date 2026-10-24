"""Checks on the shared contracts themselves.

Three other components import this module, so these tests cover the promises
the rest of the team relies on: identity conventions, the JSON wire format, and
the invariants the records enforce on their own data.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from certivity import contracts as c
from tests.fixtures.contracts import (
    PIPELINE_DRAFT,
    QUALITY_REPORT,
    RUN_INPUT,
    RUN_VIEW,
    InMemoryCorpusReader,
    ScriptedLLMAdapter,
    default_script,
)


def test_contracts_module_imports_no_feature_implementation():
    """Plan rule 5: contracts import none of the feature implementations, which
    is what keeps the import graph acyclic."""
    source = (
        __import__("pathlib").Path(c.__file__).read_text(encoding="utf-8")
    )
    for forbidden in (
        "certivity.agent",
        "certivity.corpus",
        "certivity.quality",
        "certivity.api",
        "certivity.llm",
        "import pydantic",
        "import fastapi",
        "import sqlite3",
        "import anthropic",
    ):
        assert forbidden not in source, f"contracts.py must not reference {forbidden}"


# --------------------------------------------------------------------------- #
# Identity conventions
# --------------------------------------------------------------------------- #


def test_the_same_paragraph_in_two_namespaces_has_different_ids():
    """Paragraph 2.1 in Annex 4 is not paragraph 2.1 in the main body."""
    document_id = c.document_id_for("UN R148", "03 series", "abc123def456")
    main_ns = c.namespace_for("main")
    annex_ns = c.namespace_for(("annex", "4"))

    main = c.section_id_for(document_id, main_ns, "2.1")
    annex = c.section_id_for(document_id, annex_ns, "2.1")
    assert main != annex
    assert "annex:4" in annex

    assert c.passage_id_for(document_id, main_ns, "2.1") != c.passage_id_for(
        document_id, annex_ns, "2.1"
    )


def test_namespace_grammar_covers_the_structures_in_the_real_pdfs():
    """These eight shapes all occur in the supplied regulations, so the grammar
    is settled against them rather than invented."""
    assert c.namespace_for("main") == "main"
    assert c.namespace_for(("annex", "4")) == "annex:4"
    assert c.namespace_for(("annex", "6"), ("part", "6")) == "annex:6/part:6"
    assert c.namespace_for(("annex", "4"), ("appendix", "1")) == "annex:4/appendix:1"
    # An unnumbered division is a bare kind.
    assert c.namespace_for(("annex", "5"), "appendix") == "annex:5/appendix"
    assert c.namespace_for(("appendix", "2")) == "appendix:2"
    assert c.namespace_for("introduction") == "introduction"
    assert c.namespace_for(("part", "I")) == "part:i"

    for namespace in ("main", "annex:6/part:6", "annex:5/appendix", "part:i"):
        assert c.namespace_for(*c.parse_namespace(namespace)) == namespace


def test_a_passage_id_survives_resectioning():
    """A passage ID must not depend on how the document was split into sections.

    If it did, changing section size would invalidate every cached model call,
    every saved run and every citation already exported.
    """
    document_id = c.document_id_for("UN R150", "base", "abc123def456")
    namespace = c.namespace_for(("annex", "6"), ("part", "6"))

    passage = c.passage_id_for(document_id, namespace, "3")
    assert passage == f"{document_id}#annex:6/part:6/3@001"

    # The same label under a different parent section yields the same ID.
    assert passage == c.passage_id_for(document_id, namespace, " 3 ")

    # And it can never collide with the section ID of the same label.
    assert passage != c.section_id_for(document_id, namespace, "3")

    # A second passage under one label is distinguishable.
    assert c.passage_id_for(document_id, namespace, "3", 2).endswith("@002")


def test_document_id_distinguishes_version_and_file_identity():
    base = c.document_id_for("UN R148", "base", "1111aaaa2222bbbb")
    other_version = c.document_id_for("UN R148", "latest", "1111aaaa2222bbbb")
    other_file = c.document_id_for("UN R148", "base", "9999cccc8888dddd")

    assert len({base, other_version, other_file}) == 3


def test_content_hash_is_stable_and_order_sensitive():
    assert c.content_hash("a", 1) == c.content_hash("a", 1)
    assert c.content_hash("a", 1) != c.content_hash(1, "a")


# --------------------------------------------------------------------------- #
# Record invariants
# --------------------------------------------------------------------------- #


def test_a_draft_cannot_cite_a_primary_passage_it_does_not_support():
    with pytest.raises(ValueError, match="primary_passage_id"):
        c.DraftRequirement(
            draft_id="d1",
            text="x",
            supporting_passage_ids=("p2",),
            primary_passage_id="p1",
        )


def test_a_run_needs_some_input():
    with pytest.raises(ValueError, match="specification_text"):
        c.RunInput(
            run_id="r",
            team_id="t",
            case_id="case",
            corpus_snapshot_id="snap",
            input_hash="h",
        )


def test_an_image_payload_needs_data_or_a_reference():
    with pytest.raises(ValueError, match="data_base64 or asset_ref"):
        c.ImagePayload(media_type="image/png")


def test_make_run_input_accepts_drawing_paths_and_computes_a_hash():
    run_input = c.make_run_input(
        run_id="r1",
        team_id="t1",
        case_id="case",
        corpus_snapshot_id="snap",
        specification_text="a device",
        drawing_paths=["/uploads/exploded_view.png", "/uploads/detail.jpg"],
    )
    assert [d.drawing_id for d in run_input.drawings] == ["exploded_view", "detail"]
    assert [d.media_type for d in run_input.drawings] == ["image/png", "image/jpeg"]
    assert run_input.input_hash

    # The same input yields the same hash, so a cached result stays attributable.
    again = c.make_run_input(
        run_id="r2",
        team_id="t1",
        case_id="case",
        corpus_snapshot_id="snap",
        specification_text="a device",
        drawing_paths=["/uploads/exploded_view.png", "/uploads/detail.jpg"],
    )
    assert again.input_hash == run_input.input_hash


def test_usage_merges_without_inventing_token_counts():
    measured = c.ModelUsage(calls=1, input_tokens=10, output_tokens=2, by_model={"m": 1})
    unmeasured = c.ModelUsage(calls=1, by_model={"m": 1})

    merged = measured.merged_with(unmeasured)
    assert merged.calls == 2
    assert merged.input_tokens == 10
    assert merged.by_model == {"m": 2}

    # Two unreported usages stay unreported rather than becoming zero.
    both_unknown = unmeasured.merged_with(c.ModelUsage(calls=1))
    assert both_unknown.input_tokens is None


def test_quality_report_helpers():
    report = QUALITY_REPORT
    assert report.revision_requested() is True
    findings = report.findings_for_draft(report.findings[0].target_id)
    assert findings and findings[0] is report.findings[0]
    assert report.findings_for_draft("not-a-draft") == ()


# --------------------------------------------------------------------------- #
# Wire format
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "record_type, value",
    [
        pytest.param(c.RunInput, RUN_INPUT, id="RunInput"),
        pytest.param(c.PipelineDraft, PIPELINE_DRAFT, id="PipelineDraft"),
        pytest.param(c.QualityReport, QUALITY_REPORT, id="QualityReport"),
        pytest.param(c.RunView, RUN_VIEW, id="RunView"),
    ],
)
def test_every_shared_record_survives_a_json_round_trip(record_type, value):
    blob = json.dumps(c.to_jsonable(value), ensure_ascii=False)
    assert c.from_jsonable(record_type, json.loads(blob)) == value


def test_to_jsonable_emits_plain_json_types_only():
    blob = c.to_jsonable(PIPELINE_DRAFT)

    def walk(node: Any) -> None:
        assert not isinstance(node, tuple), "tuples must become lists on the wire"
        assert not dataclasses.is_dataclass(node)
        if isinstance(node, dict):
            for key, child in node.items():
                assert isinstance(key, str)
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
        else:
            assert node is None or isinstance(node, (str, int, float, bool))

    walk(blob)
    # Enums are serialised by value, which is what the frontend types see.
    assert blob["coverage"][0]["status"] in {s.value for s in c.CoverageStatus}


def test_an_unknown_field_in_a_saved_artifact_is_ignored():
    """Adding a field to a record must not break an artifact saved earlier."""
    blob = c.to_jsonable(PIPELINE_DRAFT)
    blob["a_field_from_a_future_version"] = {"anything": [1, 2, 3]}
    assert c.from_jsonable(c.PipelineDraft, blob) == PIPELINE_DRAFT


# --------------------------------------------------------------------------- #
# Protocols
# --------------------------------------------------------------------------- #


def test_the_fixtures_satisfy_the_published_protocols():
    """If this fails, the fixtures and the contracts have drifted apart."""
    assert isinstance(InMemoryCorpusReader(), c.CorpusReader)
    assert isinstance(ScriptedLLMAdapter(script=default_script()), c.LLMAdapter)

    from certivity import agent

    assert isinstance(agent, c.ExtractionComponent)


def test_the_real_adapter_satisfies_the_llm_protocol():
    from certivity.llm import StructuredLLMAdapter

    assert isinstance(StructuredLLMAdapter(), c.LLMAdapter)


def test_fixtures_are_labelled_so_they_cannot_pass_as_real_results():
    assert PIPELINE_DRAFT.is_synthetic_fixture is True
    assert RUN_VIEW.is_fixture_data is True
    assert all("SYNTHETIC" in d.regulation for d in InMemoryCorpusReader().list_documents())


def test_runtime_code_never_reads_the_golden_answer():
    """The golden answer belongs to Teammate 3's evaluation module alone.

    This inspects the shipped source rather than trusting discipline, because an
    accidental read of ``expected.json`` would invalidate every result the
    system produces without failing any other test.

    It walks the AST and ignores docstrings and comments, so a module may state
    the rule in prose (as ``agent/pipeline.py`` does) while still being checked
    for any string literal or attribute that could actually open the file.
    """
    import ast
    from pathlib import Path

    root = Path(c.__file__).resolve().parent
    offenders: list[str] = []

    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Identify docstring nodes themselves; ast.get_docstring() re-indents the
        # text, so comparing values would not match the raw literal.
        docstring_nodes = set()
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            body = node.body
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstring_nodes.add(id(first.value))

        # A bare string expression is an attribute docstring (contracts.py uses
        # these under field definitions), which is prose, not executable code.
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list):
                continue  # e.g. a lambda, whose body is a single expression
            for child in body:
                if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                    docstring_nodes.add(id(child.value))

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstring_nodes:
                    continue
                if "expected" in node.value.lower() and ".json" in node.value.lower():
                    offenders.append(f"{path.relative_to(root)}:{node.lineno} {node.value!r}")
            if isinstance(node, ast.Name) and node.id in {
                "expected_path",
                "expected_json",
            }:
                offenders.append(f"{path.relative_to(root)}:{node.lineno} name {node.id}")

    assert not offenders, f"runtime code referenced the golden answer: {offenders}"


# --------------------------------------------------------------------------- #
# Contract 1.1.0: the ergonomics the quality component asked for
# --------------------------------------------------------------------------- #


def test_a_finding_can_be_addressed_to_a_draft_section_or_run():
    """Review raises findings about things other than drafts - a section that
    was never read, or the review itself not finishing."""
    draft = c.ReviewFinding.for_draft("drf-1", "missing_condition", "no condition")
    section = c.ReviewFinding.for_section("doc#main/3", "other", "never reviewed")
    run = c.ReviewFinding.for_run("run-1", "other", "review did not finish")

    assert draft.draft_id == "drf-1"
    assert section.draft_id is None and section.target_kind is c.TargetKind.SECTION
    assert run.draft_id is None and run.target_kind is c.TargetKind.RUN


def test_finding_ids_are_derived_when_not_supplied():
    first = c.ReviewFinding.for_draft("drf-1", "numeric_mismatch", "4 should be 12")
    same = c.ReviewFinding.for_draft("drf-1", "numeric_mismatch", "4 should be 12")
    other = c.ReviewFinding.for_draft("drf-2", "numeric_mismatch", "4 should be 12")

    assert first.finding_id.startswith("fnd-")
    assert first.finding_id == same.finding_id, "the same finding needs a stable id"
    assert first.finding_id != other.finding_id


def test_string_values_are_coerced_and_unknown_ones_rejected():
    """A caller holding string constants should not need to import the enums -
    but a typo must fail loudly rather than become 'other'."""
    finding = c.ReviewFinding(
        target_id="drf-1",
        category="duplicate",
        explanation="same obligation",
        severity="informational",
        target_kind="draft",
    )
    assert finding.category is c.FindingCategory.DUPLICATE
    assert finding.severity is c.FindingSeverity.INFORMATIONAL
    assert finding.target_kind is c.TargetKind.DRAFT

    with pytest.raises(ValueError):
        c.ReviewFinding.for_draft("drf-1", "nonexistent_category", "x")


def test_lists_are_accepted_and_stored_as_tuples():
    """The quality component builds lists; the records are frozen and compared
    by value, so they must normalise rather than drift in type."""
    report = c.QualityReport(
        run_id="run-1",
        accepted_draft_ids=["a", "b"],
        findings=[c.ReviewFinding.for_draft("a", "other", "x")],
        remaining_issues=[c.ReviewFinding.for_run("run-1", "other", "unfinished")],
    )
    assert isinstance(report.accepted_draft_ids, tuple)
    assert isinstance(report.findings, tuple)
    assert isinstance(report.remaining_issues, tuple)


def test_export_mapping_is_recorded_without_mutating_a_frozen_report():
    """Export assigns requirement ids after review; assigning onto the frozen
    record would raise, and a caller swallowing that would lose the audit
    trail from a submitted requirement back to its source evidence."""
    report = c.QualityReport(run_id="run-1", accepted_draft_ids=("drf-1",))
    assert report.export_mapping == {}

    mapped = report.with_export_mapping({"drf-1": "REQ-0001"})
    assert mapped.export_mapping == {"drf-1": "REQ-0001"}
    assert report.export_mapping == {}, "the original must be untouched"

    with pytest.raises(dataclasses.FrozenInstanceError):
        report.export_mapping = {"drf-1": "REQ-0001"}  # type: ignore[misc]


def test_remaining_issues_carry_their_target_and_evidence():
    report = QUALITY_REPORT
    assert report.remaining_issues
    issue = report.remaining_issues[0]
    assert isinstance(issue, c.ReviewFinding)
    assert issue.target_id and issue.supporting_passage_ids


def test_validation_distinguishes_our_checks_from_the_official_validator():
    """valid=True on its own is not a validated submission."""
    ours_only = c.ValidationReport(valid=True)
    assert ours_only.official_validator_ran is False

    official = c.ValidationReport(
        valid=True, official_validator_ran=True, official_validator_output="OK"
    )
    assert official.official_validator_ran is True
    assert official.official_validator_output == "OK"


def test_make_event_produces_a_run_event_with_a_timestamp():
    """emit_event takes a RunEvent, not a dict."""
    event = c.make_event("quality", "3 of 10 reviewed", completed=3, total=10)
    assert isinstance(event, c.RunEvent)
    assert event.timestamp and event.stage == "quality"
    assert event.completed == 3 and event.total == 10
