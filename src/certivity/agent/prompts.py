"""Task instructions and output schemas for extraction (owner: Anushree).

Only this module holds product, applicability, extraction and reference
prompt text. Teammate 3 owns review prompt text and keeps it in their own
component, so the two never drift into one shared mega-prompt.

Every prompt version is recorded in ``PipelineDraft.prompt_versions`` and is
part of the model-call cache key, so editing text here correctly misses the
cache instead of returning a previous run's reasoning.

Schema style: every property is ``required`` and nullable where the source may
not establish a value. A model that cannot omit a field has to say ``null``
explicitly, which is the distinction the whole pipeline depends on - "the
source does not state this" is information, and an absent key is not.
"""

from __future__ import annotations

from typing import Any

PROMPT_VERSIONS: dict[str, str] = {
    "product_profile": "v1",
    "candidate_scope": "v1",
    "document_scope": "v1",
    "extract_obligations": "v1",
    "revise_obligations": "v1",
}

_SHARED_RULES = """
Ground rules that apply to every task:

- Only the supplied source text is evidence. Do not use recalled knowledge of
  any regulation, and do not complete a quotation from memory.
- Every statement you return must be traceable to a supplied passage id.
  Never invent, guess, reformat or extrapolate a passage id: use the ids
  exactly as given. If no supplied passage supports a statement, omit the
  statement.
- Preserve numbers, units, tolerances and ranges exactly as written, including
  the decimal separator the source uses. Never convert units.
- Preserve conditions and exceptions. A requirement that applies "except for
  vehicles of category X" is not the same requirement without that clause.
- Where the source does not establish something, return null or an empty list.
  Writing a plausible value is a failure, not a fallback.
"""


def _nullable(kind: str) -> dict[str, Any]:
    return {"type": [kind, "null"]}


def _string_list() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


# --------------------------------------------------------------------------- #
# Product profile
# --------------------------------------------------------------------------- #

PRODUCT_PROFILE_INSTRUCTIONS = (
    """
You read an engineering product description, and optionally technical drawings,
and record what is actually established about the product.
"""
    + _SHARED_RULES
    + """
Task-specific rules:

- Classify every fact's `basis`:
  * "stated"   - the specification text says it.
  * "observed" - a drawing visibly shows it (a shape, an arrangement, a label
                 that is legible in the image).
  * "inferred" - it follows from stated or observed facts, with the reasoning
                 in `notes`.
  * "unknown"  - named but not established; `value` is null.
- A drawing shows geometry and arrangement. It does not establish a material
  specification, a colour standard, a photometric or electrical value, a test
  result or an approval status. Never record such a fact as "observed" from a
  drawing alone.
- `unknowns` is the most important output of this task. List the product
  properties that a regulation's scope or applicability would turn on and that
  this input does not establish - for example a vehicle category, an intended
  mounting position, a function, a voltage, or whether a device is replaceable.
  Later steps keep regulations in scope because of these, so an incomplete
  `unknowns` list silently narrows the whole analysis.
- `vehicle_categories` holds only categories the input itself states.
"""
)

_FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key", "value", "basis", "evidence", "notes"],
    "properties": {
        "key": {"type": "string", "description": "Short snake_case name of the property"},
        "value": _nullable("string"),
        "basis": {"type": "string", "enum": ["stated", "observed", "inferred", "unknown"]},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "quote", "location"],
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "'specification' or the supplied drawing id",
                    },
                    "quote": _nullable("string"),
                    "location": _nullable("string"),
                },
            },
        },
        "notes": _nullable("string"),
    },
}

PRODUCT_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "product_type",
        "vehicle_categories",
        "facts",
        "components",
        "functions",
        "materials",
        "unknowns",
        "summary",
    ],
    "properties": {
        "product_type": _nullable("string"),
        "vehicle_categories": _string_list(),
        "facts": {"type": "array", "items": _FACT_SCHEMA},
        "components": {"type": "array", "items": _FACT_SCHEMA},
        "functions": {"type": "array", "items": _FACT_SCHEMA},
        "materials": {"type": "array", "items": _FACT_SCHEMA},
        "unknowns": _string_list(),
        "summary": _nullable("string"),
    },
}


# --------------------------------------------------------------------------- #
# Applicability: candidate shortlist from the index
# --------------------------------------------------------------------------- #

CANDIDATE_SCOPE_INSTRUCTIONS = (
    """
You are shortlisting which regulations in a supplied index could govern a
product, before any regulation text has been read.
"""
    + _SHARED_RULES
    + """
Task-specific rules:

- You are given the complete allowed index. Consider every entry. Returning a
  short, confident list is a failure mode here, not a success.
- This step produces candidates, not decisions. A title is a navigation signal,
  never proof of scope; the scope text itself is read in the next step.
- Mark a regulation "candidate" when it plausibly governs this product, its
  components, its functions, its installation on a vehicle, its markings, or
  the electrical or electromagnetic behaviour of the system it belongs to.
- Mark it "candidate" - not "unlikely" - whenever the product facts needed to
  decide are listed as unknown. Uncertainty about a product fact is a reason to
  read the scope text, not a reason to exclude.
- Mark "unlikely" only when the regulation governs a plainly different kind of
  object or system, and say what makes it different in `reason`.
"""
)

CANDIDATE_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["regulation", "verdict", "reason", "depends_on_unknowns"],
                "properties": {
                    "regulation": {
                        "type": "string",
                        "description": "Regulation name copied exactly from the index",
                    },
                    "verdict": {"type": "string", "enum": ["candidate", "unlikely"]},
                    "reason": {"type": "string"},
                    "depends_on_unknowns": _string_list(),
                },
            },
        }
    },
}


# --------------------------------------------------------------------------- #
# Applicability: scope decision from the regulation's own text
# --------------------------------------------------------------------------- #

DOCUMENT_SCOPE_INSTRUCTIONS = (
    """
You decide whether one regulation applies to one product, using that
regulation's own scope, application and definition passages.
"""
    + _SHARED_RULES
    + """
Task-specific rules:

- Decide from the supplied scope and definition passages only. Cite the passage
  ids your decision rests on in `scope_evidence`; a decision with no evidence
  is not a decision.
- "applicable": the scope text covers this product, on the facts established.
- "uncertain": the scope text covers the product only under a condition that
  the product facts do not establish either way. List those facts in
  `depends_on_unknowns`. An uncertain regulation stays in the analysis.
- "excluded": the scope text positively places this product outside the
  regulation, or the regulation plainly governs a different kind of object.
  Explain which passage excludes it.
- Never answer "excluded" because a fact is unknown. Unknown means "uncertain".
- `conditions` restates, in the source's own terms, the conditions under which
  the regulation applies - for example a vehicle category, a device function,
  or a date of entry into force.
- A definition can carry an operative rule. If a definition passage imposes a
  measurable condition, say so in `notes` so the extraction step reads it.
"""
)

DOCUMENT_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "status",
        "explanation",
        "scope_evidence",
        "conditions",
        "depends_on_unknowns",
        "notes",
    ],
    "properties": {
        "status": {"type": "string", "enum": ["applicable", "uncertain", "excluded"]},
        "explanation": {"type": "string"},
        "scope_evidence": {
            "type": "array",
            "items": {"type": "string", "description": "A supplied passage id"},
        },
        "conditions": _string_list(),
        "depends_on_unknowns": _string_list(),
        "notes": _nullable("string"),
    },
}


# --------------------------------------------------------------------------- #
# Obligation extraction
# --------------------------------------------------------------------------- #

EXTRACT_OBLIGATIONS_INSTRUCTIONS = (
    """
You extract atomic regulatory obligations from one section of a regulation.
"""
    + _SHARED_RULES
    + """
Task-specific rules:

Atomicity
- One obligation per entry. A clause listing three separate limits yields three
  entries; a single limit expressed as a range ("between 4 and 12 lx") is one
  entry and must not be split, because splitting it loses the range.
- `text` is a self-contained sentence stating the obligation, including its
  values, units and its condition. A reader must be able to check it against
  the cited passage without the rest of the document.

Citation
- `supporting_passage_ids` lists every supplied passage you used, including
  context passages that carry an inherited condition.
- `primary_passage_id` must be the one passage that states the obligation
  itself. It must appear in `supporting_passage_ids`. The official output
  carries a single source, and this is it.
- If the obligation only exists by combining passages, so that no single
  passage states it, set `primary_passage_id` to the passage closest to the
  operative wording and explain the combination in `notes`. Do not silently
  cite a context passage as if it stated the rule.
- Context passages are supplied for interpretation. Do not attribute an
  obligation to a context passage when a primary passage states it.

Kind
- "mandatory": a constraint that must hold.
- "permitted_alternative": something the source allows rather than requires.
  Keep the permission as a permission; do not rewrite "may" as "shall".
- "exclusion": the source removes a case from an obligation's scope.
- "definition_rule": a definition that itself imposes a measurable condition.

Conditions and exceptions
- `applicability_condition` carries the condition under which the obligation
  applies, inherited parent conditions included. Null only for an obligation
  that is genuinely unconditional within this regulation.
- `exceptions` lists each exception separately, in the source's wording.
- `applicability_reason` says why this obligation reaches the product in front
  of you. If it reaches the product only under an unestablished condition, say
  so rather than asserting that it applies.

Values
- `values` captures each number the obligation turns on, so a reviewer can
  check it mechanically. Copy the value as written, as a string.

Tables
- A table cell is only meaningful with its row and column headers and its
  footnotes. If the supplied table context is insufficient to read a cell,
  do not guess the cell's meaning: record it in `needs_context`.

References
- `references` lists cross references in the supplied text to other paragraphs,
  annexes or regulations. Set `needed` true only when the obligation's meaning,
  scope or value cannot be stated correctly without reading the target - for
  example "shall meet the requirements of Annex 4" or "as defined in
  paragraph 2.3". Set it false for a navigational aside.
- A reference does not mean the whole referenced regulation applies. It means
  the referenced provision is incorporated for the stated purpose only.

Empty is a valid answer
- A section that states no obligation returns an empty `obligations` list. Say
  why in `section_note`. Inventing an obligation to fill the list is the worst
  possible outcome of this task.
"""
)

_VALUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["quantity", "value", "unit", "comparator"],
    "properties": {
        "quantity": _nullable("string"),
        "value": _nullable("string"),
        "unit": _nullable("string"),
        "comparator": _nullable("string"),
    },
}

_OBLIGATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "text",
        "supporting_passage_ids",
        "primary_passage_id",
        "obligation_kind",
        "applicability_condition",
        "applicability_reason",
        "exceptions",
        "values",
        "notes",
    ],
    "properties": {
        "text": {"type": "string"},
        "supporting_passage_ids": {"type": "array", "items": {"type": "string"}},
        "primary_passage_id": {"type": "string"},
        "obligation_kind": {
            "type": "string",
            "enum": [
                "mandatory",
                "permitted_alternative",
                "exclusion",
                "definition_rule",
            ],
        },
        "applicability_condition": _nullable("string"),
        "applicability_reason": _nullable("string"),
        "exceptions": _string_list(),
        "values": {"type": "array", "items": _VALUE_SCHEMA},
        "notes": _nullable("string"),
    },
}

_REFERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "origin_passage_id",
        "raw_reference",
        "target_regulation",
        "target_namespace",
        "target_paragraph",
        "needed",
        "reason",
    ],
    "properties": {
        "origin_passage_id": {"type": "string"},
        "raw_reference": {
            "type": "string",
            "description": "The reference exactly as the source writes it",
        },
        "target_regulation": _nullable("string"),
        "target_namespace": {
            "type": ["string", "null"],
            "description": "'main', or 'annex:<label>' / 'appendix:<label>' when stated",
        },
        "target_paragraph": _nullable("string"),
        "needed": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}

EXTRACT_OBLIGATIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["obligations", "references", "needs_context", "section_note"],
    "properties": {
        "obligations": {"type": "array", "items": _OBLIGATION_SCHEMA},
        "references": {"type": "array", "items": _REFERENCE_SCHEMA},
        "needs_context": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What could not be read correctly from the supplied context",
        },
        "section_note": _nullable("string"),
    },
}


# --------------------------------------------------------------------------- #
# Targeted revision from review findings
# --------------------------------------------------------------------------- #

REVISE_OBLIGATIONS_INSTRUCTIONS = (
    """
You correct previously extracted obligations in response to specific review
findings, using the original source passages.
"""
    + _SHARED_RULES
    + """
Task-specific rules:

- Each finding names a draft and a defect. Address the finding you were given;
  do not re-extract the section from scratch and do not rewrite drafts no
  finding mentions.
- For each draft, choose one action:
  * "revise" - the source supports a corrected statement. Return the corrected
    obligation in full, with its citation.
  * "withdraw" - the source does not support the obligation at all. Explain
    which passage shows that.
  * "keep"    - the finding is mistaken and the original is right. Quote the
    passage wording that settles it. This is a legitimate answer; do not change
    a correct obligation to appease a finding.
- "unresolved" is the right answer when the supplied passages are still
  insufficient. Say exactly what further section or passage is needed. Do not
  manufacture a citation to close a finding.
- A reviewer's uncertainty about a product fact is not proof that a conditional
  obligation is irrelevant. If the obligation is conditional, keep it with its
  condition intact rather than withdrawing it.
- Shortening for a length limit must not drop a value, a unit, a condition or
  an exception. If the obligation cannot be stated within the limit without
  losing one of those, answer "unresolved" and say so.
"""
)

REVISE_OBLIGATIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["draft_id", "action", "explanation", "obligation"],
                "properties": {
                    "draft_id": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["revise", "withdraw", "keep", "unresolved"],
                    },
                    "explanation": {"type": "string"},
                    "obligation": {
                        "anyOf": [_OBLIGATION_SCHEMA, {"type": "null"}],
                        "description": "Required for 'revise'; null otherwise",
                    },
                },
            },
        }
    },
}
