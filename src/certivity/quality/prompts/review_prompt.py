"""Review prompt text and output schema (owned by Teammate 3). Bump VERSION on any edit."""
VERSION = "review-v3"

REVIEW_INSTRUCTIONS = """\
You are an independent reviewer of draft regulatory obligations. For each draft you are given
the draft text, its stated applicability condition and the ACTUAL source passages cited as
evidence. Judge only from the evidence provided. Do not use outside knowledge of the regulation.
Do NOT rewrite or correct the draft; you only assess it.

For each draft, assess these checks against the evidence:
- subject: who/what must comply is the same as in the evidence
- scope: the applicability scope (vehicle category, component, situation) matches the evidence
- value: every number, limit and comparator (at least / at most / exactly) matches the evidence
- unit: units match, including any conversion implied by headers or footnotes
- condition: conditions under which the obligation applies are kept in the draft or its condition field
- exception: exceptions/alternatives stated in the evidence are not silently dropped
Use "ok", "problem", "unclear" or "not_applicable" for each check.

Rules:
- Tables: numeric or table-derived claims are only supported if the headers, units and footnotes
  needed to read them are visible. If they are not, mark the check "unclear" and say what you need.
- Number notation in the evidence: "^" marks an exponent, so "4∙10^0" is 4, "4.2∙10^1" is 42 and
  "100 cm^2" is 100 cm². A "^" plus digits directly after a word (e.g. "lamps^1") is a footnote
  marker, not a value. A draft writing the plain value (42 cd) matches evidence in this notation.
- A conditional rule stays valid when the product fact that triggers it is unknown. Never treat
  an unknown product fact as a reason to mark a conditional obligation unsupported.
- Verdict "supported": every check is ok or not_applicable. You MUST give evidence_quote, a short
  verbatim excerpt (8 to 200 characters) copied exactly from the provided evidence that supports
  the obligation.
- Verdict "unsupported": the evidence clearly does not say this (wrong subject, wrong value, wrong scope).
- Verdict "needs_revision": the evidence supports a related obligation but the draft loses or changes
  a condition, exception, unit or scope. Say exactly what is wrong in explanation.
- Verdict "needs_context": the meaning cannot be decided from the evidence given. Say in
  requested_context what passage or table would settle it.
- If you are not sure, do not choose "supported".
Return one review per draft_id, no extras.
"""

# The provider rejects a request whose schema leaves any object open: every "type": "object"
# needs "additionalProperties": False (tests/quality/test_review_schemas.py checks this).
REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reviews"],
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["draft_id", "verdict", "checks", "explanation"],
                "properties": {
                    "draft_id": {"type": "string"},
                    "verdict": {"enum": ["supported", "unsupported", "needs_revision", "needs_context"]},
                    "checks": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["subject", "scope", "value", "unit", "condition", "exception"],
                        "properties": {k: {"enum": ["ok", "problem", "unclear", "not_applicable"]}
                                       for k in ("subject", "scope", "value", "unit", "condition", "exception")},
                    },
                    "evidence_quote": {"type": ["string", "null"]},
                    "explanation": {"type": "string"},
                    "requested_context": {"type": ["string", "null"]},
                },
            },
        }
    },
}

DEDUP_INSTRUCTIONS = """\
You are given small groups of draft regulatory obligations that look textually similar.
For each group decide which drafts are GENUINE duplicates: the same obligation, same subject,
same scope, same value/unit and same conditions, only worded differently. Pick one keeper.
Drafts are NOT duplicates if they differ in a condition, exception, component, value, unit, test
procedure, or if they are independently imposed by different source passages. When unsure,
list the draft under "distinct". Only use the evidence shown.
"""

DEDUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["groups"],
    "properties": {"groups": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["group_id", "keep", "duplicates", "distinct", "explanation"],
        "properties": {"group_id": {"type": "string"}, "keep": {"type": "string"},
                       "duplicates": {"type": "array", "items": {"type": "string"}},
                       "distinct": {"type": "array", "items": {"type": "string"}},
                       "explanation": {"type": "string"}}}}},
}
