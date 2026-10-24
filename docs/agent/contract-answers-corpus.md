# Answers for the corpus component's contract proposal

**From:** Anushree (contracts owner) · **To:** Julia · **Contracts version:** 1.3.0

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


---

# Round 2: answers to the twelve follow-up questions

All of these are implemented and pushed on `feature/agent` at contracts
**1.3.0**. `git fetch origin && git merge origin/feature/agent`.

### 1. Is `contracts.py` final enough? When does it reach `main`? May I base on your branch?

**Yes, code against it**, and **yes, base `feature/corpus` on `feature/agent`** —
that is the right move today and needs no permission. The module has been stable
through three rounds of your feedback and 110 passing tests; the changes in 1.2.0
and 1.3.0 were *towards* your implementation, not away from it.

`main` is still the initial commit, which is the real problem. I am proposing we
merge `feature/agent` to `main` immediately so everyone shares a base. Until
that happens, branch off `feature/agent`.

### 2. Namespace format

**Your proposal, exactly as written.** It is now the grammar:

| Structure | Namespace |
| --- | --- |
| main body | `main` |
| Annex 4 | `annex:4` |
| Annex 6 Part 6 (R150, R48) | `annex:6/part:6` |
| Annex 4 Appendix 1 (R10, R49) | `annex:4/appendix:1` |
| unnumbered Annex 5 Appendix (R3) | `annex:5/appendix` |
| main-body Appendix 2 (R10) | `appendix:2` |
| Introduction (R148, R171) | `introduction` |
| Part I (GTR 9 rationale) | `part:i` |

Grammar: segments joined by `/`, each `kind` or `kind:label`, lowercased; an
unnumbered division is a bare kind. Built with
`namespace_for(("annex", "6"), ("part", "6"))` and reversible with
`parse_namespace()`. I dropped my `annex:4` / `appendix:4.1` scheme — it could
not express `annex:6/part:6` at all, so your real-document cases decided it.

### 3. IDs — and your stability concern

**Your concern is right and I changed the contract.** A passage ID keyed on its
section would change whenever sectioning changed, invalidating every cached
model call, every saved run and every citation already exported. That is a worse
problem than the one it solved.

Passage IDs are now **keyed on the label**:

```python
passage_id_for(document_id, namespace, label, ordinal=1)
# un-r150:base:sha256-abc#annex:6/part:6/3@001
section_id_for(document_id, namespace, label)
# un-r150:base:sha256-abc#annex:6/part:6/3
```

`ordinal` disambiguates within one label — a continuation paragraph, a table
row, or an unlabelled passage attributed to its nearest preceding label. It is
`1` for the common case. The `@NNN` suffix is always present so a passage ID can
never collide with the section ID of the same label. Please **use these
helpers**, so all four components generate byte-identical IDs.

### 4. `paragraph_label`: bare or citation form?

**Citation form** — `Annex 6 Part 6 3`, `6.10.4.2`. You build it, because only
the corpus knows the document's structure, and Teammate 3 copies it straight to
`source.paragraph`. That it already matches `expected.json` settles it.

The machine form lives in `namespace`; nobody reconstructs a citation from a
namespace. Use the **bare** label for the `label` argument of the ID helpers, so
IDs stay compact and stable.

### 5. `printed_start_page` / `page_span` as strings

**Yes, `"50"` is exactly right.** `str()` at construction is all I need.

Please keep deriving it from the footer rather than from `physical + 1`. That
the offset is constant in these PDFs is a property of this corpus, not a rule,
and your per-page verification is the thing that would catch a document where it
is not.

### 6. `get_passages` omitting unknown IDs

**Yes, switch to omitting.** The contract is "unknown IDs are omitted, not
invented". The caller already warns when a resolved reference comes back with
fewer passages than requested, so an omission is visible rather than silent.

### 7. `read_scope`: all of Definitions, or only product-relevant ones?

**All of it** — scope, application and the whole definitions clause, in document
order. Two reasons:

- Filtering by "mentions the product" needs to know the product, which is a
  semantic decision and therefore mine, not the corpus's.
- A definition that never mentions the product can still carry an operative
  condition. The plan is explicit that definitions are not blanket-excluded, and
  the live run confirmed it: a definition imposing a 1,5-degree limit was
  correctly extracted as a requirement.

Volume is my problem, not yours: I cap scope passages per decision and warn when
I truncate.

### 8. Tables: is caption + column headers enough?

**Ship caption + header row now** — it is far better than nothing and unblocks
you.

Footnotes are worth a follow-up if you get time, and I can show you why rather
than assert it. In the live run the model produced *"the values applying at
25 °C (footnote (a))"* **only** because the footnote text was in
`table_context`. Without it a cell reads as an unconditional limit when it is
actually conditional — a wrong requirement that looks perfectly citable.

A cheap interim step: if a cell's text contains a footnote marker, append any
footnote-looking text found on the same page. Imprecise, but it preserves the
qualification instead of dropping it. Please also set `is_table` explicitly;
keep the `kind:table` flag too.

### 9. `page_refs` reasons

**Moot — I removed `page_refs` and took your field instead.** `SectionBundle`
now has `visual_pdf_page_indices: tuple[int, ...]` and `PageRef` is gone. A
viewer gets the printed label from `get_page_asset`, so wrapping each index in a
record only duplicated that lookup. No reason strings needed.

### 10. `list_documents` order

**index.json order preferred, but it is not load-bearing** — I group by
regulation and sort internally, so your numeric sort would not break anything.
Index order is simply more reproducible against the supplied file. Do not spend
time on it if something else is pending.

### 11. The three extra methods

**`get_document` and `list_reference_candidates` are now in the `CorpusReader`
Protocol.** `search` is a separate optional `CorpusSearch` Protocol, because
search must never be required of an implementation — systematic reading is the
coverage mechanism, and search only assists navigation and reference
resolution.

`list_reference_candidates` is **already wired in and tested**: extraction takes
the union of the references the model reported and the ones you detected,
deduplicated on the request key. There is a test proving the annex reference is
still followed when the model reports no references at all. This directly
improves reference recall, because a reference the model fails to notice is
otherwise silently lost. Thank you for proposing it — it was the most valuable
item in your proposal.

### 12. Still blocking me

A corpus snapshot. Your tests need
`data/participant-package/regulations/index.json`, so 14 real-corpus tests skip
and 4 snapshot tests error here. Please send the exact bootstrap command and a
rough build time for the base role. Extraction against real regulation text is
the one thing in my component that has never been tested, and the held-out case
opens tomorrow at 08:00.
