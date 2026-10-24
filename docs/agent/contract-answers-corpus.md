# Answers for the corpus component's contract proposal

**From:** Anushree (contracts owner) · **To:** Julia · **Contracts version:** 1.2.0

Your `docs/corpus/contract-proposal.md`, answered point by point. I verified
these by merging `feature/corpus` into `feature/agent` and diffing your records
against mine field by field, so this is measured, not guessed.

**Your corpus already binds to the shared module** — `certivity.corpus._contracts`
resolves `Document` from `certivity.contracts`, so `_provisional_contracts.py`
is dormant and can be deleted once you fetch. That also means the mismatches
below are live `TypeError`s today, not future work.

## I changed the contract to your names (no edits needed from you)

Your naming was better in four places, so the contract moved rather than your code:

| Was | Now | Why |
| --- | --- | --- |
| `Section.number` | `Section.paragraph_label` | Matches your spelling and `SourcePassage.paragraph_label`; "number" was vague. |
| `Section.passage_ids` | `Section.primary_passage_ids` | Yours is explicit and matches `SectionBundle.primary_passages`. |
| `SourcePassage.printed_page_span` | `SourcePassage.page_span` | Your shorter name. `pdf_page_indices` already names the physical one unambiguously. |
| `ReferenceRequest.origin_passage_id` | `ReferenceRequest.originating_passage_id` | Yours matches the master plan's wording. |
| `SectionBundle.page_refs` | `SectionBundle.visual_pdf_page_indices` | Yours. A viewer gets the printed label from `get_page_asset`, so wrapping each index in a record only duplicated that lookup. I dropped `PageRef`. |

`ReferenceResolution.request` is now **optional**. You don't need to echo the
request back — `follow_references` backfills it from what it sent.

## Please change these (in priority order)

**1. `SourcePassage.namespace` — currently unset, so every passage reads `"main"`.**
You have the namespace on `Section`; please put it on the passage too. The
extraction prompt shows the model each passage's namespace, so without it an
Annex 6 Part 6 passage is presented as main-body text. Highest priority.

**2. `ReferenceRequest.target_annex` → `target_namespace`.** One kwarg. Your
field already carries a namespace string, and `target_annex` holding
`"Appendix 2"` or `"Part I"` is actively misleading.

**3. `is_table` and `table_context`.** You flag `kind:table` in `parsing_flags`,
which I'll still see, but the explicit fields matter: `table_context` is where
the row/column headers and footnotes go, and the first live run showed the model
uses them — it produced *"the values applying at 25 °C (footnote (a))"* only
because the footnote was supplied. A table cell without its headers is not
interpretable, so please populate both.

**4. `Section.ordinal`** (and `depth` if cheap). `build_inspection_plan` sorts on
`ordinal` to walk a document in reading order. It falls back to
`(depth, paragraph_label)`, so this is not fatal, but a correct `ordinal` is what
makes coverage order match the document.

**5. `Document.index_entry`.** Verbatim `index.json` entry. Applicability
screening reads it to shortlist candidates; without it, screening sees only
titles, and the plan is explicit that a title is never scope proof. You already
load the index in `registry.py`.

**6. `snapshot_id` as a property** on `SqliteCorpusReader`. One line. The
pipeline warns when the reader's snapshot disagrees with the run input.

**7. `printed_start_page` and `page_span` as `str`, not `int`.** A printed label
is not always an integer — front matter uses roman numerals — and a string can
always carry an integer label. `str()` at construction is enough.

## Your decisions, confirmed

1. **`Section.namespace` + `paragraph_label`** — confirmed, and I adopted
   `paragraph_label`. Your readable namespace values (`main`, `Annex 6 Part 6`,
   `Annex 4 Appendix 1`, `Part I`) are **better than my scheme** and I dropped
   mine: `namespace_for("annex", "4")` could not express "Annex 6 Part 6" at all.
   The namespace is now an opaque string the corpus owns, and the extraction
   prompt tells the model to copy it exactly as supplied rather than prescribing
   a format.
2. **`paragraph_label` as the citation form** — confirmed. Export copies it to
   `source.paragraph` directly.
3. **`printed_start_page` is the page to cite, verified against the footer** —
   confirmed, and verifying every page against the footer is exactly right.
   `page_span` printed, `pdf_page_indices` physical zero-based: confirmed.
4. **`kind:table` as a flag** — please also set the explicit fields, per item 3
   above. Keep the flags; they're useful.
   `context:parent` / `context:definition` on bundle context passages: keep, no
   schema change needed, and genuinely useful.
5. **`visual_pdf_page_indices`** — adopted, your name.
6. **`target_annex`** — rename per item 2. `"this annex"` as a literal is fine;
   resolve it against the originating passage's namespace. `UN R10` style for
   `target_regulation`: confirmed.
7. **Protocol additions — all three accepted** into `CorpusReader`:
   - `get_document(document_id)` — yes.
   - `list_reference_candidates(passage_ids)` — **the most valuable thing in your
     proposal.** Reference following currently depends on the model spotting a
     cross reference in the text. A regex pre-pass means recall no longer rests
     on that, which directly improves what the plan calls reference-following
     coverage. I want to use it.
   - `search(query, document_id=None, limit=20)` — yes, for reference resolution
     and navigation. Not as a coverage mechanism; systematic reading stays the
     coverage mechanism.
8. **`list_documents(role=None)`** — confirmed, useful for the optional task.

## What I need to finish Checkpoint 2

Your tests need `data/participant-package/regulations/index.json`, which isn't
in the repo, so 14 real-corpus tests skip and 4 snapshot tests error. Once a
snapshot exists I can run extraction against real regulation text — that is the
one thing in my component that has never been tested, and the deadline is
tomorrow. Please say how to build one (`python -m certivity.corpus bootstrap
--fetch-missing`?) and roughly how long a base-role build takes.

Everything else on my side is verified: 107 tests passing, and the prompts are
validated against a live model — which found and fixed three real bugs,
including a provider schema limit that made every real run fail.
