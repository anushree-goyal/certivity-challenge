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
    main = c.section_id_for(document_id, c.namespace_for("main"), "2.1")
    annex = c.section_id_for(document_id, c.namespace_for("annex", "4"), "2.1")

    assert main != annex
    assert "annex:4" in annex
    assert c.passage_id_for(main, 1) != c.passage_id_for(annex, 1)


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
