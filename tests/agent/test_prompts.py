"""Checks on the prompt schemas themselves.

The provider rejects a schema with more than 16 union-typed parameters, which
only a real call reveals: a scripted fixture happily accepts any schema. That
failure cost a full run - it aborted before the first obligation - so the limit
is asserted here instead.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from certivity.agent import prompts as p
from certivity.llm.schema_check import schema_errors

UNION_LIMIT = 16
"""Provider cap on union-typed parameters per schema. Exceeding it is a 400:
"Schemas contains too many parameters with union types ... This causes
exponential compilation cost."
"""

HEADROOM = 12
"""Stay below this when adding fields, so the next addition does not hit 400."""

ALL_SCHEMAS = {
    "product_profile": p.PRODUCT_PROFILE_SCHEMA,
    "candidate_scope": p.CANDIDATE_SCOPE_SCHEMA,
    "document_scope": p.DOCUMENT_SCOPE_SCHEMA,
    "extract_obligations": p.EXTRACT_OBLIGATIONS_SCHEMA,
    "revise_obligations": p.REVISE_OBLIGATIONS_SCHEMA,
}


def count_unions(node: Any) -> int:
    """Count union-typed parameters the way the provider does: per occurrence.

    A sub-schema reused in four places is expanded four times in the serialised
    request, so it costs four slots - which is exactly how the product profile
    reached 18 from a four-union fact schema used four times.
    """
    total = 0
    if isinstance(node, dict):
        if isinstance(node.get("type"), list):
            total += 1
        if "anyOf" in node or "oneOf" in node:
            total += 1
        for value in node.values():
            total += count_unions(value)
    elif isinstance(node, list):
        for value in node:
            total += count_unions(value)
    return total


@pytest.mark.parametrize("name, schema", sorted(ALL_SCHEMAS.items()))
def test_schema_is_within_the_provider_union_limit(name, schema):
    unions = count_unions(schema)
    assert unions <= UNION_LIMIT, (
        f"{name} has {unions} union-typed parameters, over the provider's "
        f"{UNION_LIMIT}; the call will fail with HTTP 400. Make optional fields "
        f"plain types left out of 'required' instead of nullable unions."
    )
    assert unions <= HEADROOM, (
        f"{name} has {unions} unions, within the limit but with little headroom"
    )


@pytest.mark.parametrize("name, schema", sorted(ALL_SCHEMAS.items()))
def test_schema_is_serialisable_and_well_formed(name, schema):
    json.dumps(schema)
    assert schema["type"] == "object"
    assert schema.get("additionalProperties") is False, (
        f"{name} must close additionalProperties so an invented field is rejected"
    )
    for field in schema.get("required", []):
        assert field in schema["properties"], f"{name}: required field {field} is undefined"


@pytest.mark.parametrize("name, schema", sorted(ALL_SCHEMAS.items()))
def test_a_minimal_answer_satisfies_each_schema(name, schema):
    """Every optional field must genuinely be optional.

    Builds the smallest answer containing only required fields and checks it
    validates. If a field the code reads is required but the prompt treats it as
    optional, this catches the mismatch.
    """

    def minimal(node: dict[str, Any]) -> Any:
        kind = node.get("type")
        if isinstance(kind, list):
            kind = kind[0]
        if kind == "object":
            return {
                field: minimal(node["properties"][field])
                for field in node.get("required", [])
            }
        if kind == "array":
            return []
        if kind == "string":
            return node.get("enum", ["x"])[0]
        if kind == "boolean":
            return False
        if kind in ("integer", "number"):
            return 0
        return None

    assert schema_errors(schema, minimal(schema)) == []


def test_load_bearing_nullables_are_still_required():
    """Two fields must stay required-and-nullable.

    An explicit null on these is a claim the pipeline acts on, so letting the
    model omit them would silently lose information:

    * a product fact with a null value is what keeps a regulation in scope;
    * a null applicability_condition asserts the obligation is unconditional.
    """
    fact = p.PRODUCT_PROFILE_SCHEMA["properties"]["facts"]["items"]
    assert "value" in fact["required"]
    assert fact["properties"]["value"]["type"] == ["string", "null"]

    obligation = p.EXTRACT_OBLIGATIONS_SCHEMA["properties"]["obligations"]["items"]
    assert "applicability_condition" in obligation["required"]
    assert obligation["properties"]["applicability_condition"]["type"] == [
        "string",
        "null",
    ]


def test_prompts_name_no_specific_regulation_or_expected_count():
    """No prompt may hardcode a case's answer."""
    texts = [
        p.PRODUCT_PROFILE_INSTRUCTIONS,
        p.CANDIDATE_SCOPE_INSTRUCTIONS,
        p.DOCUMENT_SCOPE_INSTRUCTIONS,
        p.EXTRACT_OBLIGATIONS_INSTRUCTIONS,
        p.REVISE_OBLIGATIONS_INSTRUCTIONS,
    ]
    for text in texts:
        lowered = text.lower()
        for forbidden in ("r148", "r48", "r150", "r10", "rear lamp", "190 requirement"):
            assert forbidden not in lowered, f"a prompt mentions {forbidden!r}"


def test_every_task_has_a_recorded_prompt_version():
    assert set(p.PROMPT_VERSIONS) == set(ALL_SCHEMAS)
    assert all(v for v in p.PROMPT_VERSIONS.values())
