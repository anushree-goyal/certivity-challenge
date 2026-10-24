# Shared contracts — published reference

**Owner:** Anushree. **Module:** [`src/certivity/contracts.py`](../../src/certivity/contracts.py).
**Version:** `CONTRACTS_VERSION = "1.1.0"`.

Everyone imports from this module. Do not make a local copy with different
field names. To change a contract: propose it to this owner, who updates the
definition and the fixtures and tells the team, per plan rule 6.

```python
from certivity.contracts import PipelineDraft, DraftRequirement, CorpusReader
```

## Two things to know first

**No dependencies.** `contracts.py` is standard library only — plain
`dataclasses`, no pydantic. It imports none of the feature implementations, so
it cannot create a circular import, and you can import it before any
dependency decision is settled.

**Records are frozen.** Every record is a frozen dataclass, so a record you
receive cannot be mutated behind your back. Build a changed copy instead
(`dataclasses.replace`, or construct a new one).

## Identity conventions

These are the conventions the whole team depends on. Getting one wrong produces
a citation that looks valid and is not.

| Concept | Rule |
| --- | --- |
| `pdf_page_index` | **Zero-based physical page index** into the supplied PDF. Always. |
| `printed_*_page` | The label **printed on the page** of the supplied Certivity PDF, as a **string** — labels are not always integers. The official submission's `source.page` is a printed page. |
| Paragraph identity | A paragraph label alone never identifies a passage. `2.1` in Annex 4 and `2.1` in the main body are different sections. |
| Nullability | `None` means "not established". It is not the same as `""` or `[]`. |

Use the supplied id helpers so all four components generate byte-identical ids:

```python
document_id_for(regulation, version_label, checksum)   # un-r148:03-series:sha256-9f2c1a4b7d0e
namespace_for("annex", "4")                            # annex:4
section_id_for(document_id, namespace, number)         # <document_id>#annex:4/2.1
passage_id_for(section_id, ordinal)                    # <section_id>@003
content_hash(*parts)                                   # stable sha256 for input hashes and cache keys
```

## The public Python boundary

```python
# Teammate 2 implements CorpusReader; everyone else only calls it.
list_documents(role="base") -> list[Document]
read_scope(document_id) -> list[SourcePassage]
list_sections(document_id) -> list[Section]
read_section(section_id, include_context=True) -> SectionBundle
get_passages(passage_ids) -> list[SourcePassage]
resolve_reference(request: ReferenceRequest) -> ReferenceResolution
get_page_asset(document_id, pdf_page_index) -> PageAsset

# Anushree
generate_json(task, instructions, payload, schema, images=None, *,
              prompt_version="v1", max_output_tokens=None) -> LLMResponse
run_extraction(run_input, corpus, llm, emit_event) -> PipelineDraft
revise_extraction(run_input, draft, findings, corpus, llm, emit_event) -> PipelineDraft

# Teammate 3
review_requirements(run_input, draft, corpus, llm, emit_event) -> QualityReport
build_submission(run_input, draft, report, corpus) -> dict
```

`CorpusReader`, `LLMAdapter`, `ExtractionComponent` and `QualityComponent` are
`runtime_checkable` Protocols, so `isinstance(my_reader, CorpusReader)` is a
real check you can assert in your own tests.

## Notes per role

### Teammate 2 — corpus

- Implement the `CorpusReader` Protocol. Your reader must expose
  `snapshot_id` as a property.
- `SectionBundle` separates `primary_passages` (what the section says) from
  `context_passages` (inherited conditions and definitions, supplied for
  interpretation only). The extractor will not attribute an obligation to a
  context passage without flagging it, so the split matters.
- `resolve_reference` must return `ReferenceStatus.AMBIGUOUS` with several
  `candidate_passage_ids` rather than silently picking one. The agent handles
  ambiguity; it cannot detect a wrong silent choice.
- `Document.index_entry` carries the verbatim `index.json` entry. Applicability
  screening reads it, so please pass it through rather than dropping it.
- `SourcePassage.table_context` should carry the row/column headers and
  footnotes needed to read a table cell. Without it the extractor records
  "cannot read this cell" instead of guessing.
- Server-side file paths are deliberately **not** in `Document`. Serve files
  through `get_page_asset` / Teammate 4's routes.

### Teammate 3 — quality and export

- You receive `PipelineDraft` and return `QualityReport`.
- `DraftRequirement.primary_passage_id` is the single citation for the official
  output; `supporting_passage_ids` may be longer. The primary id is always
  included in the supporting list (enforced in `__post_init__`).
- `DraftRequirement.values` holds `NumericValue` entries as **strings**, so
  `0,5` and tolerances survive. Use them for mechanical numeric checks.
- `DraftRequirement.unresolved_references` tells you an obligation depends on a
  target nobody could resolve.
- `notes` may say `"Primary citation is a context or referenced passage rather
  than a primary passage of this section."` — that is a flag raised for you.
- Build findings with the constructors, which set the addressing for you:

  ```python
  ReviewFinding.for_draft(draft_id, "missing_condition", "...")
  ReviewFinding.for_section(section_id, "other", "never reviewed")
  ReviewFinding.for_run(run_id, "other", "review did not finish")
  ```
  `category`, `severity` and `target_kind` accept their plain string values and
  are coerced; an unrecognised value raises rather than becoming `"other"`.
  `finding_id` is derived from the content when you leave it empty, and the
  `draft_id` property returns `None` for a non-draft finding.
- `requested_context` holds section or passage **ids**, not prose — the agent
  resolves them (a passage id is fetched, a section id pulls in that section's
  primary and context passages). Put prose in `explanation`.
- `remaining_issues` is `tuple[ReviewFinding, ...]`, not strings, so an
  unsettled issue keeps its target and evidence.
- Records are **frozen**. Record export ids with
  `report.with_export_mapping({draft_id: requirement_id})`, which returns a
  copy; assigning onto a built report raises `FrozenInstanceError`.
- Lists are accepted anywhere a tuple is declared and normalised on
  construction, so you can build with lists.
- `ValidationReport.official_validator_ran` and `official_validator_output`
  separate "our checks passed" from "the organizers' validator passed". A
  report with `valid=True` and `official_validator_ran=False` must never be
  presented as a validated submission.
- `emit_event` takes a `RunEvent`, not a dict. Use
  `contracts.make_event(stage, message, completed=..., total=...)`.
- Use the same `LLMAdapter` with your own prompt text. Keep review prompts in
  your component.
- `EvaluationReport.semantic_score` and `platform_score` must stay `None`
  unless actually measured.

### Teammate 4 — application

- `RunInput` field names: `run_id`, `team_id`, `case_id`, `corpus_snapshot_id`,
  `input_hash`, `specification_text`, `drawings`, `config`. For upload paths use
  the helper rather than building `DrawingRef` yourself:

  ```python
  from certivity.contracts import make_run_input
  run_input = make_run_input(
      run_id=..., team_id=..., case_id=..., corpus_snapshot_id=...,
      specification_text=..., drawing_paths=[...], config=...,
  )
  ```
  It is keyword-only, infers each media type from the file extension, and
  computes `input_hash` when omitted. `RunInput` raises `ValueError` if there is
  neither specification text nor a drawing, so validate the upload first.
- Build the adapter with `certivity.llm.create_llm(...)` (alias of
  `adapter_from_env`). `adapter.describe()` returns a secret-free dict suitable
  for `GET /api/health`, including `credentials_configured`.
- Serialise everything with `to_jsonable` / `from_jsonable`. Tuples become
  lists, enums become their values, and unknown keys are ignored on read, so an
  artifact saved by an older build still loads.
- `RunView.is_fixture_data` and `PipelineDraft.is_synthetic_fixture` are how you
  keep fixture mode visibly separate in the UI. Please surface them.
- Progress: pass an `emit_event` callback taking a `RunEvent`. The agent works
  if it does nothing, and an exception inside your callback will not fail a run.

## Fixtures

Synthetic, labelled, and importable today — use them before real ingestion
exists. Nothing here is real data or a real model result.

```python
from tests.fixtures.contracts import (
    InMemoryCorpusReader,   # a complete CorpusReader over an invented 2-document corpus
    ScriptedLLMAdapter,     # a complete LLMAdapter, deterministic and offline
    default_script,         # scripted answers for the agent's five tasks
    RUN_INPUT, PIPELINE_DRAFT, QUALITY_REPORT, RUN_VIEW,  # one valid example of each
)
```

`InMemoryCorpusReader` deliberately reproduces the awkward cases: the same
paragraph label `2.1` in the main body and in Annex 1, a passage spanning two
printed pages, a table cell meaningless without its headers, printed labels
that differ from physical indices, and three references — one resolvable, one
ambiguous, one outside the corpus.

`ScriptedLLMAdapter` validates each scripted answer against the caller's
schema, so a drifted fixture fails in your test rather than producing output the
real adapter would reject.
