"""SYNTHETIC scripted LLM adapter (owner: Anushree).

**A fixture, not a model.** It returns canned answers so that extraction,
review and the application can be exercised deterministically, offline and for
free. Nothing it produces is a model result.

It validates every scripted answer against the caller's schema, so a fixture
that drifts away from a schema fails in the test rather than producing output
the real adapter would have rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from certivity.contracts import (
    ImagePayload,
    LLMAdapter,
    LLMCallError,
    LLMResponse,
    ModelBudgetExceeded,
    ModelUsage,
)
from certivity.llm.schema_check import schema_errors

Responder = Callable[[Any], Any]
"""Takes the call payload, returns the data to answer with."""

FIXTURE_MODEL = "synthetic-fixture-model"


def _omit_absent(schema: dict[str, Any], data: Any) -> Any:
    """Drop ``None`` values for fields the schema marks optional.

    A real structured-output model cannot return null for a field typed
    ``string``: it omits the key instead. The scripts below are written with
    ``None`` for readability, so this normalises them to what the provider would
    actually send - and keeps the fixture honest about the difference between
    "omitted" and an explicit null.

    Fields in ``required`` are left alone, because a null there is load-bearing
    (a product fact with no value, an unconditional obligation).
    """
    if isinstance(data, dict) and isinstance(schema, dict):
        required = set(schema.get("required", []))
        properties = schema.get("properties", {})
        out: dict[str, Any] = {}
        for key, value in data.items():
            if value is None and key not in required:
                continue
            sub = properties.get(key)
            out[key] = _omit_absent(sub, value) if isinstance(sub, dict) else value
        return out
    if isinstance(data, list) and isinstance(schema, dict):
        items = schema.get("items")
        if isinstance(items, dict):
            return [_omit_absent(items, item) for item in data]
    return data


@dataclass
class RecordedCall:
    task: str
    payload: Any
    prompt_version: str
    image_count: int


@dataclass
class ScriptedLLMAdapter:
    """Implements ``contracts.LLMAdapter`` from a task -> answer script."""

    script: dict[str, Any | Responder] = field(default_factory=dict)
    calls: list[RecordedCall] = field(default_factory=list)
    max_calls: int | None = None
    strict: bool = True
    """Raise on a task with no scripted answer, rather than inventing one."""

    def generate_json(
        self,
        task: str,
        instructions: str,
        payload: Any,
        schema: dict[str, Any],
        images: Sequence[ImagePayload] | None = None,
        *,
        prompt_version: str = "v1",
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        if self.max_calls is not None and len(self.calls) >= self.max_calls:
            raise ModelBudgetExceeded(
                f"fixture budget of {self.max_calls} calls is exhausted"
            )
        self.calls.append(
            RecordedCall(
                task=task,
                payload=payload,
                prompt_version=prompt_version,
                image_count=len(images or ()),
            )
        )

        if task not in self.script:
            if self.strict:
                raise LLMCallError(f"the fixture script has no answer for task {task!r}")
            data: Any = {}
        else:
            entry = self.script[task]
            data = entry(payload) if callable(entry) else entry

        data = _omit_absent(schema, data)
        errors = schema_errors(schema, data)
        if errors:
            raise AssertionError(
                f"the scripted answer for {task!r} violates the caller's schema: "
                + "; ".join(errors[:5])
            )

        return LLMResponse(
            data=data,
            provider="fixture",
            model=FIXTURE_MODEL,
            task=task,
            latency_ms=0.0,
            usage=ModelUsage(calls=1, input_tokens=0, output_tokens=0, by_model={FIXTURE_MODEL: 1}),
            warnings=("synthetic fixture adapter: this is not a model result",),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "provider": "fixture",
            "model": FIXTURE_MODEL,
            "is_synthetic_fixture": True,
            "calls_used": len(self.calls),
        }

    def tasks_called(self) -> list[str]:
        return [call.task for call in self.calls]


def _fact(key: str, value: str | None, basis: str, quote: str | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "value": value,
        "basis": basis,
        "evidence": [{"source": "specification", "quote": quote, "location": None}]
        if quote
        else [],
        "notes": None,
    }


def default_script() -> dict[str, Any | Responder]:
    """A script that answers the agent component's five tasks coherently.

    The extraction responder reads the passage ids it was actually given, so the
    citation-integrity checks in ``agent.extract`` are exercised for real rather
    than against a constant.
    """
    from tests.fixtures.contracts import corpus as syn

    def product_profile(payload: Any) -> dict[str, Any]:
        return {
            "product_type": "synthetic signalling device",
            "vehicle_categories": ["M1"],
            "facts": [
                _fact("colour", "red", "stated", "emits red light"),
                _fact("marking", None, "unknown"),
            ],
            "components": [_fact("lens", "polycarbonate lens", "stated", "lens")],
            "functions": [_fact("signalling", "signals vehicle presence", "stated", "signal")],
            "materials": [],
            "unknowns": ["luminous_intensity", "marking"],
            "summary": "A synthetic rear signalling device for an M1 vehicle.",
        }

    def candidate_scope(payload: Any) -> dict[str, Any]:
        candidates = []
        for entry in payload["regulation_index"]:
            regulation = entry["regulation"]
            is_signalling = "signalling" in (entry.get("index_entry") or {}).get(
                "scope_hint", ""
            )
            candidates.append(
                {
                    "regulation": regulation,
                    "verdict": "candidate" if is_signalling else "unlikely",
                    "reason": (
                        "The index entry describes signalling devices, which is what this "
                        "product is."
                        if is_signalling
                        else "The index entry describes a different kind of object."
                    ),
                    "depends_on_unknowns": [],
                }
            )
        return {"candidates": candidates}

    def document_scope(payload: Any) -> dict[str, Any]:
        scope_ids = [p["passage_id"] for p in payload["scope_and_definition_passages"]]
        return {
            "status": "applicable",
            "explanation": (
                "The scope passage covers signalling devices of category SX on M1 "
                "vehicles, which matches the product."
            ),
            "scope_evidence": scope_ids[:1],
            "conditions": ["device is a category SX signalling device on an M1 vehicle"],
            "depends_on_unknowns": ["marking"],
            "notes": "Paragraph 2.2 defines the reference axis with a 1,5 degree limit.",
        }

    def extract_obligations(payload: Any) -> dict[str, Any]:
        primary = {p["passage_id"]: p for p in payload["primary_passages"]}
        referenced = {p["passage_id"]: p for p in payload["referenced_passages"]}
        obligations: list[dict[str, Any]] = []
        references: list[dict[str, Any]] = []

        if A_SPEC_COLOUR := primary.get(syn.A_SPEC_COLOUR.passage_id):
            obligations.append(
                {
                    "text": "The light emitted by the device shall be red.",
                    "supporting_passage_ids": [A_SPEC_COLOUR["passage_id"]],
                    "primary_passage_id": A_SPEC_COLOUR["passage_id"],
                    "obligation_kind": "mandatory",
                    "applicability_condition": (
                        "device is a category SX signalling device on an M1 vehicle"
                    ),
                    "applicability_reason": "The product is a signalling device.",
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
                }
            )

        if intensity := primary.get(syn.A_SPEC_INTENSITY.passage_id):
            # One obligation, keeping the range together and the exception attached.
            obligations.append(
                {
                    "text": (
                        "The luminous intensity shall be between 4 cd and 12 cd when "
                        "measured along the reference axis, except for devices bearing "
                        'the marking "R2", for which it shall be between 2 cd and 6 cd.'
                    ),
                    "supporting_passage_ids": [intensity["passage_id"]]
                    + [
                        p["passage_id"]
                        for p in payload["context_passages"]
                        if p["paragraph_label"] == "2.2"
                    ],
                    "primary_passage_id": intensity["passage_id"],
                    "obligation_kind": "mandatory",
                    "applicability_condition": (
                        "device is a category SX signalling device on an M1 vehicle"
                    ),
                    "applicability_reason": "The product is a signalling device.",
                    "exceptions": [
                        'devices bearing the marking "R2": between 2 cd and 6 cd'
                    ],
                    "values": [
                        {
                            "quantity": "luminous intensity",
                            "value": "4 to 12",
                            "unit": "cd",
                            "comparator": "between",
                        },
                        {
                            "quantity": "luminous intensity (R2 marking)",
                            "value": "2 to 6",
                            "unit": "cd",
                            "comparator": "between",
                        },
                    ],
                    "notes": None,
                }
            )

        if ref := primary.get(syn.A_SPEC_REFERENCE.passage_id):
            references.extend(
                [
                    {
                        "originating_passage_id": ref["passage_id"],
                        "raw_reference": "Annex 1, paragraph 2.1",
                        "target_regulation": None,
                        "target_namespace": "annex:1",
                        "target_paragraph": "2.1",
                        "needed": True,
                        "reason": (
                            "The test procedure's conditions are needed to state the "
                            "obligation."
                        ),
                    },
                    {
                        "originating_passage_id": ref["passage_id"],
                        "raw_reference": "SYNTHETIC REG Z",
                        "target_regulation": "SYNTHETIC REG Z",
                        "target_namespace": None,
                        "target_paragraph": "1",
                        "needed": True,
                        "reason": "The referenced regulation's provisions are incorporated.",
                    },
                ]
            )
            annex = referenced.get(syn.A_ANNEX_P1.passage_id)
            if annex is not None:
                obligations.append(
                    {
                        "text": (
                            "The device shall comply with the test procedure of Annex 1, "
                            "paragraph 2.1, measured at an ambient temperature of 25 "
                            "°C after 30 minutes of operation."
                        ),
                        "supporting_passage_ids": [
                            ref["passage_id"],
                            annex["passage_id"],
                        ],
                        "primary_passage_id": ref["passage_id"],
                        "obligation_kind": "mandatory",
                        "applicability_condition": (
                            "device is a category SX signalling device on an M1 vehicle"
                        ),
                        "applicability_reason": "The product is a signalling device.",
                        "exceptions": [],
                        "values": [
                            {
                                "quantity": "ambient temperature",
                                "value": "25",
                                "unit": "°C",
                                "comparator": "=",
                            }
                        ],
                        "notes": None,
                    }
                )

        if annex_table := primary.get(syn.A_ANNEX_TABLE.passage_id):
            obligations.append(
                {
                    "text": (
                        "For device category SX-1 the luminous intensity shall be between "
                        "4 cd and 12 cd, measured at 25 °C."
                    ),
                    "supporting_passage_ids": [annex_table["passage_id"]],
                    "primary_passage_id": annex_table["passage_id"],
                    "obligation_kind": "mandatory",
                    "applicability_condition": "device category SX-1",
                    "applicability_reason": "The product may be of category SX-1.",
                    "exceptions": [],
                    "values": [
                        {
                            "quantity": "luminous intensity",
                            "value": "4 to 12",
                            "unit": "cd",
                            "comparator": "between",
                        }
                    ],
                    "notes": "Read with Table 1 headers and footnote (a).",
                }
            )

        # Keyed on the passage id, not the bare label: paragraph 2.2 exists in
        # both the main body and Annex 1, and matching on the label alone would
        # cite the annex table for a main-body definition.
        definitions = [
            p
            for p in payload["primary_passages"]
            if p["passage_id"] == syn.A_DEF_P2.passage_id
        ]
        for definition in definitions:
            obligations.append(
                {
                    "text": (
                        "The reference axis marked on the device shall not deviate by more "
                        "than 1,5 degrees from the geometric centre line."
                    ),
                    "supporting_passage_ids": [definition["passage_id"]],
                    "primary_passage_id": definition["passage_id"],
                    "obligation_kind": "definition_rule",
                    "applicability_condition": None,
                    "applicability_reason": "The definition imposes a measurable limit.",
                    "exceptions": [],
                    "values": [
                        {
                            "quantity": "reference axis deviation",
                            "value": "1,5",
                            "unit": "degrees",
                            "comparator": "<=",
                        }
                    ],
                    "notes": None,
                }
            )

        return {
            "obligations": obligations,
            "references": references,
            "needs_context": [],
            "section_note": None if obligations else "no obligation stated in this section",
        }

    def revise_obligations(payload: Any) -> dict[str, Any]:
        decisions = []
        for item in payload["drafts"]:
            decisions.append(
                {
                    "draft_id": item["draft_id"],
                    "action": "keep",
                    "explanation": (
                        "The cited passage states the obligation as written; the finding "
                        "is not borne out by the source."
                    ),
                    "obligation": None,
                }
            )
        return {"decisions": decisions}

    return {
        "product_profile": product_profile,
        "candidate_scope": candidate_scope,
        "document_scope": document_scope,
        "extract_obligations": extract_obligations,
        "revise_obligations": revise_obligations,
    }
