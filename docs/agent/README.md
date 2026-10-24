# Extraction and reasoning component

**Owner:** Anushree. **Branch:** `feature/agent`. Section 1 of the build plan.

Owned paths: `src/certivity/agent/`, `src/certivity/llm/`,
`src/certivity/contracts.py`, `tests/agent/`, `tests/fixtures/contracts/`,
`docs/agent/`.

## What it does

```python
from certivity.agent import run_extraction, revise_extraction

draft  = run_extraction(run_input, corpus, llm, emit_event)
better = revise_extraction(run_input, draft, findings, corpus, llm, emit_event)
```

Both are synchronous and know nothing about HTTP, SQLite, the submission format
or the UI. Run them in a background worker.

Stages, in order:

1. **Product profile** — specification text and/or drawings to `ProductProfile`,
   recording facts, their basis, and the unknowns that later scope decisions
   turn on.
2. **Applicability** — screen the *complete* allowed index for candidates, then
   decide each candidate from that regulation's own scope and definition
   passages, citing the evidence.
3. **Inspection plan** — walk the selected documents in reading order. Coverage
   comes from systematic reading, not retrieval top-k.
4. **Extraction** — atomic obligations per section batch, with inherited
   conditions, definitions and table context.
5. **Reference following** — resolve the references the extractor says it needs,
   re-read the section with the targets available, and record what stayed
   unresolved.
6. **Revision** — act on Teammate 3's findings, touching only the named drafts.

## What it refuses to do

These are enforced in code, not left to the prompt:

- An obligation citing a passage id that was never supplied is **dropped**. A
  fabricated source id is worse than a missing requirement — it looks checkable
  and is not.
- A product fact claimed as *observed from a drawing* while naming a measured or
  certified property (material, photometric value, voltage, approval) is
  **downgraded to inferred** and flagged.
- An **exclusion with no cited scope passage becomes uncertain**. Exclusion is
  the only verdict that removes a regulation from the run, so it is the one
  verdict that must rest on evidence.
- A regulation the screening step never mentioned is **kept as a candidate**, not
  dropped.
- Uncertainty about a product fact never becomes "this regulation does not
  apply". Uncertain regulations stay in the analysis by default.
- Definitions are **not** blanket-excluded; a definition can carry an operative
  measurement rule.
- A missing credential raises `LLMConfigurationError`. There is no silent
  fallback to invented output.
- Nothing here reads `expected.json`. A test walks the AST of every shipped
  module to enforce it.

## Honesty of the output

`PipelineDraft.partial` is `True` unless every planned section ended
`PROCESSED` or `EXCLUDED`. A capped run, an exhausted model budget, a corpus
read failure and a failed section all degrade coverage and leave the unread
sections visible as `PENDING` — they never disappear from the ledger.

**Coverage means recorded inspection status. It is not a claim of exhaustive
legal compliance.**

## Running the tests

No installed package and no network needed; the tests use the synthetic corpus
and the scripted adapter.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install pytest anthropic
.venv/bin/python -m pytest tests/agent/ -q
```

`anthropic` is needed only for `tests/agent/test_llm_adapter.py`, which injects
a fake client and makes no paid call. The other 51 tests need nothing but
`pytest`.

## Model configuration

See [dependencies.md](dependencies.md). Credentials are resolved by the SDK and
are never copied into this process's records; `adapter.describe()` is
secret-free and safe to serve.
