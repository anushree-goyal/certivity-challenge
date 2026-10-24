# Corpus component (Teammate 2)

Turns the supplied regulation PDFs into a local, read-only SQLite corpus and exposes it through `CorpusReader`.
Other components never touch the SQL; they call the reader.

## Setup and commands

Python 3.11+ with `pymupdf`. `certivity.contracts` (Anushree) must be importable (PDF text, tables and page rendering). `certifi` is optional and is only used for the
download step on macOS python.org builds. `pytest` is needed for the tests. Run with `src` on `PYTHONPATH` until
Teammate 4's package scaffolding is merged.

```bash
# 1. participant package (unchanged upstream checkout, pinned at the commit we verified)
git clone https://github.com/Certivity/industrial-ai-hackathon data/participant-package
git -C data/participant-package checkout 2104a8e   # or `git pull` on Sunday for the held-out case

# 2. download base PDFs with the package's own tool (checksums verified there)
PYTHONPATH=src python -m certivity.corpus fetch --role base

# 3. parse and publish a snapshot (about 1.5 to 7 minutes cold, depending on machine load; seconds when cached)
PYTHONPATH=src python -m certivity.corpus build

# inspection
PYTHONPATH=src python -m certivity.corpus status
PYTHONPATH=src python -m certivity.corpus documents
PYTHONPATH=src python -m certivity.corpus sections 'un-r48:9-3-0:sha256-d4b1dde87bf8'
PYTHONPATH=src python -m certivity.corpus section 'un-r48:9-3-0:sha256-d4b1dde87bf8#main/6.10'
PYTHONPATH=src python -m certivity.corpus resolve 'un-r48:9-3-0:sha256-d4b1dde87bf8#main/6.10.5.1@001' 'paragraph 5.8.1. above'
PYTHONPATH=src python -m certivity.corpus page 'un-r48:9-3-0:sha256-d4b1dde87bf8' 49
PYTHONPATH=src python -m certivity.corpus search 'rear position lamp height' --document 'un-r48:9-3-0:sha256-d4b1dde87bf8'
PYTHONPATH=src python -m certivity.corpus diagnostics --summary
PYTHONPATH=src python -m certivity.corpus prune --keep 2      # manual; never deletes CURRENT

# tests (real-corpus tests skip when no snapshot is built)
PYTHONPATH=src python -m pytest tests/corpus
```

Environment overrides: `CERTIVITY_PARTICIPANT_PACKAGE` (default `data/participant-package`) and
`CERTIVITY_CORPUS_CACHE` (default `data/cache/corpus`).

## Public interface

Records are Anushree's `certivity.contracts` (1.3.0, merged into this branch from `feature/agent`): `Document`,
`SourcePassage`, `Section`, `SectionBundle`, `PageAsset`, `ReferenceRequest`, `ReferenceResolution`, `DocumentRole`,
`ReferenceStatus`. The reader satisfies both published protocols, `CorpusReader` and the optional `CorpusSearch`.

```python
from certivity.corpus import open_corpus, corpus_status, ensure_corpus

corpus = open_corpus("data/cache/corpus")   # CURRENT snapshot, read-only; snapshot_id="snap-..." pins one
corpus.snapshot_id                       # record with each run
corpus.list_documents(role="base")       # -> list[Document] in index.json order; role=None lists every imported role
corpus.read_scope(document_id)           # -> list[SourcePassage]: title, Scope clause, then the Definitions clause
corpus.list_sections(document_id)        # -> list[Section] in reading order (systematic coverage starts here)
corpus.read_section(section_id, include_context=True)   # -> SectionBundle
corpus.get_passages(passage_ids)         # -> list[SourcePassage] in request order; unknown ids are omitted (contract)
corpus.resolve_reference(ReferenceRequest)               # -> ReferenceResolution
corpus.get_page_asset(document_id, pdf_page_index)       # -> PageAsset (PNG rendered on demand, cached)
```

`get_document` and `list_reference_candidates` are part of `CorpusReader` (1.3.0); `search` is `CorpusSearch`. Corpus-only extras:

| Method | For |
| --- | --- |
| `list_reference_candidates(passage_ids) -> list[ReferenceRequest]` | (`CorpusReader`) references detected at import, ready to pass to `resolve_reference` |
| `search(query, document_id=None, limit=20)` | SQLite FTS5 navigation aid. Not a coverage mechanism |
| `get_document(document_id)`, `diagnostics(document_id=None)`, `status()` | Metadata, parse diagnostics, readiness |
| `get_document_path(document_id) -> Path` | **Server-only** original PDF path for the backend's `/pdf` route (`UnknownId` / `FileNotFoundError`) |
| `corpus_status(corpus_dir=None) -> dict` (module function) | Health-route readiness for a corpus folder (default `data/cache/corpus`). Never raises; `ready: False` with a `reason` when unavailable |
| `bootstrap(package_dir, corpus_dir, fetch_missing=False) -> dict` | Startup: optional fetch, then build or reuse a snapshot; returns status (`ready`, `snapshot_id`, counts) |

Errors: `CorpusNotReady` (no completed snapshot) and `UnknownId` (a `KeyError` subclass).

Threading: the reader opens one SQLite connection per thread with `mode=ro&immutable=1`. One reader object can be
shared by a worker's threads. Writes fail.

## IDs and citation fields

| Thing | Format (contract helper) | Example |
| --- | --- | --- |
| document | `document_id_for(regulation, version, checksum)` | `un-r48:9-3-0:sha256-d4b1dde87bf8` |
| section | `section_id_for(document, namespace, number)`; a namespace root uses `root` | `...#main/6.10`, `...#annex:6/part:6/root` |
| passage | `passage_id_for(document, namespace, label, ordinal)` | `...#main/6.10.4.2@001`, `...#annex:6/part:6/3.2@002` (its table) |

- **Namespaces** on records are the contract's machine form (`namespace_for`): `main`, `annex:4`, `annex:6/part:6`,
  `annex:4/appendix:1`, `annex:5/appendix` (an unnumbered appendix, R3), `appendix:2` (an appendix to the main body,
  R10), `introduction` (R148, R171) and `part:i` (the non-normative GTR 9 rationale). They keep repeated labels apart.
  `resolve_reference` accepts this form or the human one (`Annex 6, Part 6`) in `target_namespace`.
- **Passage IDs are keyed on the label, not the section**, so they survive re-sectioning. A passage takes its own
  label, else the nearest preceding label in its namespace, else `_` (text before the first label: headings, front
  matter). Within one label the paragraph is `@001`; its tables, captions, footnotes and continuations follow from
  `@002` in reading order.
- **`paragraph_label`** (on passages and sections) is the citation form, ready to copy to `source.paragraph`, in the
  same form as `expected.json`: `6.10.4.2` or `Annex 6 Part 6 3`. Unlabelled annex text carries the division
  (`Annex 4`) and the flag `label_from_namespace`. Unlabelled main-body text (title, front matter) has `None`.
- **Pages:** every page of every supplied PDF has a footer `UN R48, version 9.3.0 | page 50 of 122`. The printed page
  is read **from that footer**, page by page, and checked against the physical position (it matches on all 3,029
  pages; a page where it did not would be reported, not assumed). `printed_start_page` is a string (`"50"`) and is
  what `source.page` cites; `page_span` lists every printed page as strings; `pdf_page_indices` are zero-based.
- **`Section`**: `paragraph_label`, `primary_passage_ids` in reading order, `depth` (0 = namespace root),
  `ordinal` (position in document reading order).
- **Tables**: `is_table=True`. `table_context` holds the caption, the column-heading row, and footnote-like text from
  the table's page(s): footnote passages on that page, plus short lines right after the table that start with a
  marker (`(a)`, `a)`, `*`, `1/`, `Note`). This is an interim rule until footnotes are tied to their table at parse
  time; it over-includes on purpose, because a dropped footnote turns a conditional cell into an unconditional limit.
- **`parsing_flags`** on a `SourcePassage`:
  - `kind:paragraph|heading|intro|table|caption|footnote|front_matter` marks the passage type.
  - `context:parent|context:definition` appears only on bundle context passages.
  - `spans_pages`, `page_has_visual_content`, `caption:<text>`, `table_without_caption`, `continuation_after_table`,
    `label_out_of_sequence`, `label_gap`, `duplicate_label`, `label_without_dot` and `layout_text_outside_margin`
    describe layout and parsing.
  - `scope_heading` and `scope_section_not_detected` relate to scope detection.

## How sections and bundles are built

- **Sections are adaptive.** Each namespace has a root section. A paragraph whose whole subtree (sub-paragraphs,
  tables, footnotes) fits 6,000 characters is one section. A larger paragraph keeps its own text, and its children
  become sections of their own. Example: R48 `6.10 Rear position lamp` is one section with all of 6.10.x, while
  `6. Individual specifications` is split. Small annex Parts are one section each. Result: 6,280 sections, median
  600 characters, 95th percentile 4.4k.
- **`SectionBundle`** has these parts:
  - `primary_passages`: the section's own passages in reading order. This is what extraction is measured against.
  - `context_passages`, kept separate: the heading passages of every ancestor section (e.g. `6. Individual
    specifications`, `Annex 6`, `Part 6 - ...`), plus up to 20 definitions whose quoted term occurs in the primary
    text (`"Rear position lamp" means ...`).
  - `visual_pdf_page_indices`: zero-based pages of the primary passages that have images, tables or substantial
    vector drawings, for a vision call when needed.
  - `notes`: warnings such as a section over 12k characters, or a section number inferred from its sub-paragraphs.
- **Tables** become their own passages with the caption (`Table A6-1 ...`) and one row per line, cells joined by
  ` | `. Cell text is read with a clip, because PyMuPDF's `Table.extract()` drops decimal points (`0.51 ± 0.02`
  became `051±002`).
- **Captions** (`Figure I`, `Table A6-1 ...`) become separate `kind:caption` passages and do not interrupt the
  paragraph around them (R148 3.3.1.2 continues after its figure). A table's caption is part of the table passage.
- **Footnotes** become separate passages in the section where they appear. They are de-duplicated when the
  renderer repeats one per marker. A paragraph interrupted by a footnote or a page break continues in the same
  passage.

## Reference resolution

The reader follows these rules:

- The target document is the originating one, or the **base** document of the named regulation (`Regulation No. 10`
  goes to `UN R10` base). A regulation not in the index returns `outside_corpus`.
- A whole-regulation reference resolves to that regulation's scope passages. The explanation states that the
  reference alone does not make the whole regulation apply, and that the series of amendments may differ.
- Namespace:
  - "of this annex" means the originating annex.
  - "Annex 4" / "Part 2 of Annex 6" / "Annex 3, Part 7" / "Appendix 1 to Annex 5" name the namespace explicitly.
  - "of this Regulation", or any reference from the main body, means `main`.
  - A plain "paragraph 2." from inside an annex searches both `main` and the annex. If both contain it, the result
    is **`ambiguous`** with both candidates; the reader does not guess.
- An absent label returns `missing`, naming the nearest existing ancestor. Labels flagged as duplicates return
  `ambiguous`. A range (`2.2.1.1. to 2.2.1.4.`) resolves to its endpoints.

## Snapshots and storage

`data/cache/corpus/` (ignored by git) contains:

```
CURRENT                      id of the snapshot new runs should open
snapshots/<id>/corpus.sqlite tables: documents, pages, sections, passages, definitions, reference_candidates,
                             import_diagnostics, meta, passages_fts
snapshots/<id>/manifest.json identity: index sha256, package commit, parser version, code fingerprint, file lists, counts
parsed/<pdf sha256>.<parser version>.json.gz   per-PDF parse cache
renders/<pdf sha256>/<page>@<dpi>.png          page images
```

- The snapshot id is a hash of the schema version, parser version, corpus code fingerprint, index hash, roles and
  the imported document ids. An identical build reuses the snapshot.
- Snapshots are built in a temporary directory and renamed into place when complete, so a running job never sees a
  partial or changed snapshot.
- New files or code produce a new id. The old snapshot stays readable until it is pruned manually.
- Bump `PARSER_VERSION` in `parse.py` when parse output changes; the parse cache is keyed by it.

## Verified on the real base corpus (snapshot `snap-80cf23669d98`)

- **Acquisition:** 56 of 56 base PDFs downloaded and checksum-verified, with no failures. The `latest` versions and
  the sub-goal old/new PDFs have **not** been downloaded yet, and the links expire after the event.
- **Import:** 56 documents, 3,029 pages, 31,881 passages (including 576 figure/table captions), 6,265 sections,
  1,519 definitions and 13,065 reference candidates. There were no parse failures.
- **Pages:** printed-page footers match physical positions on 3,029 of 3,029 pages, and the regulation/version
  in each footer matches the index.
- **Paragraphs:** all 190 cited paragraphs in `cases/dev-rear-lamp/expected.json` (R148, R48, R150, R10) exist in the
  corpus with the cited page inside their page span. This includes `Annex 6 Part 6 3. Table A6-1`. The check was
  a one-off developer script that is not part of the component; runtime code never reads `expected.json`.
  Teammate 3 independently ran the official scorer on the rear-lamp output: page recall 1.000.
- **Contracts 1.3.0 migration:** every one of the 11,556 distinct detected references resolves exactly as before the
  migration (same status, same target passages, compared by citation rather than ID). Anushree's model-free agent
  paths run on the real corpus: the inspection plan covers 668 sections of R48/R148/R150/R10, and following
  corpus-detected references from 400 of them resolves 94% (572 of 607).
- **Tests:** `tests/corpus` has 36 tests; with Anushree's tests the repository runs 117 passed, 3 skipped (billed
  real-model tests). Corpus tests cover structure rules, snapshot behaviour (missing file, checksum mismatch, reuse,
  refresh keeps the old snapshot) and the real corpus:
  - metadata, footer verification and contract-shaped records (IDs, namespaces, enums, index order);
  - R48 6.10.4.2 on page 50, a cross-page paragraph, `1.1` repeated across R150 annex Parts;
  - table decimals and `table_context` footnotes;
  - bundle context and visual pages, scope with Definitions, captions kept out of paragraphs;
  - every resolution status, including `annex:6/part:6`-style targets;
  - rendering, read-only access, threads, and both published protocols.

## Known parsing gaps (from `diagnostics --summary`)

| Diagnostic | Count | Where / meaning |
| --- | ---: | --- |
| `label_out_of_sequence` | 164 | Mostly GTR 9 Part I (the rationale has interleaved numbering), R49, and "Notes: 1. 2." lists under figures. The passage is kept and flagged |
| `label_gap` | 89 | Numbering skips (deleted or reserved paragraphs). Informational |
| `label_unexpected_start` | 48 | A namespace's first label is not `1.` (e.g. `0.` or `A.2.1.` in R49). Kept |
| `empty_page` | 20 | Figure-only pages. Image available through `get_page_asset` |
| `duplicate_label` | 11 | The same label twice in one namespace. `resolve_reference` returns `ambiguous` for these |
| `namespace_repeated` | 9 | An annex heading repeated (R49 mostly) |

Other gaps:

- Text inside figures (`layout_text_outside_margin`) is kept but is noisy.
- Tables without ruling lines are not detected as tables. Their cells appear as flagged off-margin text.
- Very long unlabelled tables or forms make 11 sections larger than 12k characters (R49 Annex 4 Appendix 1, R128
  Annex 1, R49 Annex 13, ...).
- A table caption attaches to the paragraph open at that point (`3.2` rather than `3.` in R150 Annex 6 Part 6).
