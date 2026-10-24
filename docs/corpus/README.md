# Corpus component (Teammate 2)

Turns the supplied regulation PDFs into a local, read-only SQLite corpus and exposes it through `CorpusReader`.
Other components never touch the SQL; they call the reader.

## Setup and commands

Python 3.11+ with `pymupdf` (PDF text, tables and page rendering). `certifi` is optional and is only used for the
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
PYTHONPATH=src python -m certivity.corpus sections 'UN_R48@9.3.0~UN_R48_base~d4b1dde8'
PYTHONPATH=src python -m certivity.corpus section 'UN_R48@9.3.0~UN_R48_base~d4b1dde8/sec/main/6.10'
PYTHONPATH=src python -m certivity.corpus resolve 'UN_R48@9.3.0~UN_R48_base~d4b1dde8/p/main/6.10.5.1' 'paragraph 5.8.1. above'
PYTHONPATH=src python -m certivity.corpus page 'UN_R48@9.3.0~UN_R48_base~d4b1dde8' 49
PYTHONPATH=src python -m certivity.corpus search 'rear position lamp height' --document 'UN_R48@9.3.0~UN_R48_base~d4b1dde8'
PYTHONPATH=src python -m certivity.corpus diagnostics --summary
PYTHONPATH=src python -m certivity.corpus prune --keep 2      # manual; never deletes CURRENT

# tests (real-corpus tests skip when no snapshot is built)
PYTHONPATH=src python -m pytest tests/corpus
```

Environment overrides: `CERTIVITY_PARTICIPANT_PACKAGE` (default `data/participant-package`) and
`CERTIVITY_CORPUS_CACHE` (default `data/cache/corpus`).

## Public interface

```python
from certivity.corpus import open_corpus, corpus_status, ensure_corpus

corpus = open_corpus("data/cache/corpus")   # CURRENT snapshot, read-only; snapshot_id="snap-..." pins one
corpus.snapshot_id                       # record with each run
corpus.list_documents(role="base")       # -> list[Document]; role=None lists every imported role
corpus.read_scope(document_id)           # -> list[SourcePassage]: title + Scope clause and its sub-paragraphs
corpus.list_sections(document_id)        # -> list[Section] in reading order (systematic coverage starts here)
corpus.read_section(section_id, include_context=True)   # -> SectionBundle
corpus.get_passages(passage_ids)         # -> list[SourcePassage], same order; raises UnknownId for any unknown id
corpus.resolve_reference(ReferenceRequest)               # -> ReferenceResolution
corpus.get_page_asset(document_id, pdf_page_index)       # -> PageAsset (PNG rendered on demand, cached)
```

Additions beyond the planned seven methods (listed in `contract-proposal.md`):

| Method | For |
| --- | --- |
| `list_reference_candidates(passage_ids) -> list[ReferenceRequest]` | References detected at import, ready to pass to `resolve_reference` |
| `search(query, document_id=None, limit=20)` | SQLite FTS5 navigation aid. Not a coverage mechanism |
| `get_document(document_id)`, `diagnostics(document_id=None)`, `status()` | Metadata, parse diagnostics, readiness |
| `get_document_path(document_id) -> Path` | **Server-only** original PDF path for the backend's `/pdf` route (`UnknownId` / `FileNotFoundError`) |
| `corpus_status()` (module function) | Health-route readiness. Never raises |
| `bootstrap(package_dir, corpus_dir, fetch_missing=False) -> dict` | Startup: optional fetch, then build or reuse a snapshot; returns status (`ready`, `snapshot_id`, counts) |

Errors: `CorpusNotReady` (no completed snapshot) and `UnknownId` (a `KeyError` subclass).

Threading: the reader opens one SQLite connection per thread with `mode=ro&immutable=1`. One reader object can be
shared by a worker's threads. Writes fail.

## IDs and citation fields

| Thing | Format | Example |
| --- | --- | --- |
| document | `<regulation>@<version>~<file stem>~<sha256[:8]>` | `UN_R48@9.3.0~UN_R48_base~d4b1dde8` |
| section | `<document>/sec/<namespace>/<label or root>` | `.../sec/Annex6Part6/root`, `.../sec/main/6.10` |
| passage | `<document>/p/<namespace>/<label or _kind>[_dupN]` | `.../p/main/6.10.4.2`, `.../p/Annex6Part6/3.2/table:A6-1` |

- **Namespaces** keep repeated labels apart: `main`, `Annex 4`, `Annex 6 Part 6`, `Annex 4 Appendix 1`, `Appendix 2`
  (an appendix to the main body), `Introduction` (R148, R171), and `Part I` (the non-normative GTR 9 rationale).
- **`paragraph_label`** is the citation string, in the same form as `expected.json`: `6.10.4.2` or
  `Annex 6 Part 6 3`. Unlabelled annex text carries the namespace (`Annex 4`) and the flag `label_from_namespace`.
  Unlabelled main-body text (title, front matter) has `null`.
- **Pages:** every page of every supplied PDF has a footer `UN R48, version 9.3.0 | page 50 of 122`. The parser
  checks that footer against the physical position; it matches on all 3,029 pages. So
  `printed_start_page = pdf_page_index + 1`, and the printed page is what `source.page` cites. `pdf_page_indices`
  are zero-based physical indices for rendering. `page_span` is printed pages.
- **`parsing_flags`** on a `SourcePassage`:
  - `kind:paragraph|heading|intro|table|footnote|front_matter` marks the passage type.
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
  - `visual_pdf_page_indices`: pages of the primary passages that have images, tables or substantial vector
    drawings, for a vision call when needed.
- **Tables** become their own passages with the caption (`Table A6-1 ...`) and one row per line, cells joined by
  ` | `. Cell text is read with a clip, because PyMuPDF's `Table.extract()` drops decimal points (`0.51 ± 0.02`
  became `051±002`).
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

## Verified on the real base corpus (snapshot `snap-e3d43fb50521`)

- **Acquisition:** 56 of 56 base PDFs downloaded and checksum-verified, with no failures. The `latest` versions and
  the sub-goal old/new PDFs have **not** been downloaded yet, and the links expire after the event.
- **Import:** 56 documents, 3,029 pages, 31,305 passages, 6,280 sections, 1,519 definitions and 13,071 reference
  candidates. There were no parse failures.
- **Pages:** printed-page footers match physical positions on 3,029 of 3,029 pages, and the regulation/version
  in each footer matches the index.
- **Paragraphs:** all 190 cited paragraphs in `cases/dev-rear-lamp/expected.json` (R148, R48, R150, R10) exist in the
  corpus with the cited page inside their page span. This includes `Annex 6 Part 6 3. Table A6-1`. The check was
  a one-off developer script that is not part of the component; runtime code never reads `expected.json`.
- **Tests:** `tests/corpus` has 28 tests covering structure rules, snapshot behaviour (missing file, checksum
  mismatch, reuse, refresh keeps the old snapshot) and the real corpus:
  - metadata and footer verification;
  - R48 6.10.4.2 on page 50;
  - a cross-page paragraph;
  - `1.1` repeated across R150 annex Parts;
  - table decimals;
  - bundle context;
  - every resolution status;
  - rendering;
  - read-only access and threads.

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
