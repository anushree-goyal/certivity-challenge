# Answers for the quality component's contract questions

**From:** Anushree (contracts owner) · **To:** Teammate 3 · **Contracts version:** 1.1.0

I could not find `contract-proposal.md` — it is not on `origin/feature/quality`,
`origin/main` or `origin/feature/agent`. Please push it if there is more than
what is below. In the meantime I answered from your `src/certivity/quality/_records.py`
stand-in, which states your requirements precisely.

## First: `contracts.py` already exists

It has been on `origin/feature/agent` since before your first commit, and is
now at version 1.1.0. Your `try: from certivity.contracts import ...` will
start succeeding as soon as you fetch:

```bash
git fetch origin
git merge origin/feature/agent        # or rebase onto it
```

The reference is `docs/agent/contracts.md`. Everything below is implemented and
pushed, not proposed.

## Accepted — I changed the contract to match you

Four of your stand-in's decisions were better than mine. All four are in 1.1.0.

| Your field | What I did |
| --- | --- |
| `export_mapping` | **Added** to `QualityReport` as `dict[str, str]` (draft_id → requirement_id). It is the audit trail from a submitted requirement back to its source evidence, and it was missing. |
| `official_validator_ran`, `official_validator_output` | **Added** to `ValidationReport`. You are right that "our checks passed" and "the organizers' validator passed" are different claims. |
| `remaining_issues` as findings | **Changed** from `tuple[str, ...]` to `tuple[ReviewFinding, ...]`. A remaining issue that keeps its target and evidence beats a sentence nobody can act on. |
| `duplicate_mapping` | **Renamed** from my `duplicate_map` to your spelling. No behaviour change; it just reads better. |

## Changes needed on your side

### 1. `set_export_mapping` will always fail — use `with_export_mapping`

The records are frozen dataclasses, so `report.export_mapping = mapping` raises
`FrozenInstanceError`. Your helper catches it and returns `False`, which means
today the mapping is silently lost and you emit a warning instead. Replace it:

```python
report = report.with_export_mapping(result.export_mapping)   # returns a copy
```

In `export.py:88` that becomes an assignment rather than an `if not ...:` branch.

### 2. `make_finding(None, ...)` needs an explicit run-level target

Addressing is `target_kind` + `target_id`, because a finding can concern a
section ("never reviewed") or the review itself, not only a draft. Your
`review.py:59` passes `draft_id=None` for `REVIEW_INCOMPLETE`; that is a
run-level finding:

```python
ReviewFinding.for_run(run_id, "other", " | ".join(gaps),
                      severity="informational")
ReviewFinding.for_draft(draft_id, "missing_condition", explanation)
ReviewFinding.for_section(section_id, "other", "never reviewed")
```

`finding_id` is derived from the content when omitted, so you do not need to
invent ids. The `draft_id` property still works and returns `None` for
non-draft findings, so your existing draft-level filtering is unaffected.

### 3. `category` must be one of the defined values

`category` accepts a plain string and is coerced, so you need not import the
enum — but an unrecognised value **raises** rather than becoming `"other"`.
Map your constants onto these:

`unsupported_by_source`, `missing_condition`, `numeric_mismatch`,
`wrong_source_version`, `unresolved_source`, `duplicate`, `not_atomic`,
`out_of_scope`, `context_requested`, `too_long`, `other`

Your `DUPLICATE_REMOVED` / `POSSIBLE_DUPLICATE_UNRESOLVED` / `SEM_UNREVIEWED` /
`REVIEW_INCOMPLETE` constants need mapping — I suggest `duplicate` for the first
two and `other` for the last two, with the detail in `explanation`. If you want
dedicated values for any of them, ask and I will add them; that is a one-line
change and a better outcome than overloading `other`.

Same for `severity`: `blocking`, `revision_requested`, `informational`.

### 4. `requested_context` is ids, not prose

Your stand-in types it as `str | None`. In the contract it is a tuple of
**section or passage ids**, because `revise_extraction` resolves them: a passage
id is fetched directly, and a section id pulls in that section's primary and
context passages. A sentence here resolves to nothing. Prose about what is
missing belongs in `explanation`; `requested_correction` stays free text.

This is the one place I kept my design over yours, and it is because the field
is load-bearing on my side rather than a matter of taste.

### 5. `make_validation_error` returns a dict on the real contracts

In your `_records.py`, the `USING_SHARED_CONTRACTS` branch returns
`{"path": ..., "message": ...}`. `ValidationReport.errors` expects
`contracts.ValidationError` records, and a dict will serialise into something
Teammate 4's frontend types do not match. Return the real record:

```python
from certivity.contracts import ValidationError
return ValidationError(path=path, message=message)   # `code` is optional
```

### 6. `emit` must send a `RunEvent`, not a dict

`emit_event` is typed `Callable[[RunEvent], None]`. I added a helper so you do
not have to format the timestamp:

```python
from certivity.contracts import make_event
emit_event(make_event("quality", f"{n} drafts reviewed", completed=n, total=total))
```

### 7. `QualityReport` needs `run_id`

Your `make_report` does not take one. It is required, because a report that
cannot be tied to a run cannot be correlated with the draft, the artifacts or
the snapshot it came from. One extra argument.

## Two things you may not have noticed

**`DraftRequirement.values`** is a tuple of `NumericValue(quantity, value, unit,
comparator)` with `value` kept as a **string**, so `0,5` and tolerances survive
unchanged. That is there for your numeric-mismatch checks — you should not have
to re-parse numbers out of `text`.

**`notes` may carry a flag for you.** When extraction cites a context or
referenced passage as primary, it appends: `"Primary citation is a context or
referenced passage rather than a primary passage of this section."` That is a
deliberate signal that the citation may not support the obligation on its own.

## Fixtures you can use today

```python
from tests.fixtures.contracts import (
    InMemoryCorpusReader,   # a complete CorpusReader, no PDFs needed
    ScriptedLLMAdapter, default_script,
    PIPELINE_DRAFT,         # a valid draft to review, labelled synthetic
    QUALITY_REPORT,         # a valid report in the 1.1.0 shape
)
```

`PIPELINE_DRAFT` deliberately includes a draft with an unresolved reference, a
conditional obligation with an exception, and a definition carrying a
measurable rule — the three cases your review logic has to handle. The corpus
contains the same paragraph label in two namespaces, so a dedup or citation
check that keys on a bare label will fail there rather than in the demo.

## Please propose, don't fork

Four of your six requests changed the contract, so proposing works. If you need
another field, ask rather than keeping a divergent stand-in: a second definition
of `QualityReport` that drifts is the one failure mode the single-owner rule
exists to prevent. Once you have merged `feature/agent` you can delete the
fallback block in `_records.py` entirely; keeping the `make_*` helpers is fine
and still a good idea.
