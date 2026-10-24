# Quality on the real corpus: first check (2026-10-24)

**What ran:** review, export, the official validator and the official local scorer on a real corpus snapshot.
**What did not:** extraction and semantic review (no model credential). The drafts are a labelled
STAND-IN: one draft per base passage containing "shall", text copied verbatim. The numbers below
describe that stand-in and the citation pipeline, not extraction quality.

## Setup

- Participant package 2104a8e; base PDFs of UN R10, R48, R148, R150 and latest R10, R48 (official fetch tool, checksums verified).
- Corpus built with Teammate 2's importer (`feature/corpus` d76052b, parser `corpus-parse-8`): snapshot `snap-4cbe1d63690c`, 6 documents, 652 pages, 1,184 sections, 5,692 passages, 0 parse failures.
- Integration worktree = `feature/quality` + `feature/corpus`, with a **local-only patch** to `corpus/reader.py` (`_passage`, `_section`) so it builds canonical contract records (see "Corpus contract gaps").
- Harness: `real_review.py` (kept outside the repo; reads `expected.json` only through `evaluate_submission_detailed`).

## Results

| Step | Result |
| --- | --- |
| Review (no model) | 1,252 drafts in 0.1 s: 1,245 accepted (deterministic only), 1 rejected, 1 duplicate removed, 5 too long; `partial=True` |
| Export | 1,245 requirements, 0.6 MB, nothing excluded |
| Official validator | valid (1,245 requirements) |
| Official local scorer (stand-in) | regulation P/R/F1 1.000 / 1.000 / 1.000; page P/R/F1 0.202 / 1.000 / 0.335 |

Page recall 1.000 means every expected (regulation, version, page) is cited by some real passage, so the corpus's printed-page mapping and the export's citation agree with the official answer. Regulation scores are trivially perfect because only the four expected regulations were loaded. Low page precision is expected for a stand-in that cites every "shall" passage.

## Acceptance cases (real passages)

| Case | Draft | Outcome |
| --- | --- | --- |
| Wrong version | R10 latest (7.3.0) passage | rejected: `wrong_version_or_document` |
| Unsupported value | R148 3.3.1.2 "at least 5 mm" changed to "57 mm" | flagged: `numeric_not_in_evidence` |
| Lost condition | "...unladen condition except for necessary test equipment" with the exception removed | flagged: `possible_lost_condition` |
| Genuine duplicate | exact repeat of an R150 obligation | removed, mapped to the original |
| Distinct conditions | same R150 passage, conditions "class A" / "class B" | both kept |

The flagged cases stay accepted until semantic review decides them; without a model the report is `partial`.

## Observations on real text

- 89 primary passages span pages (`primary_spans_pages`): the cited start page may not be where the obligation is.
- 5 "shall" passages exceed 2,000 characters; extraction must split or reformulate them (export never truncates).
- 22 near-duplicate groups stay unresolved without a model (all kept).
- A figure caption is merged into a paragraph's text (R148 3.3.1.2 ends with "Figure I Examples of the arrangement ..."); reported to Teammate 2.

## Corpus contract gaps (reported to Teammate 2)

`feature/corpus` still builds several records with provisional field names, which raise `TypeError` with `certivity.contracts`:

- `Section`: `paragraph_label`, `primary_passage_ids` -> contract `number`, `passage_ids` (+ `depth`, `ordinal`).
- `SourcePassage`: `page_span` -> `printed_page_span` (string labels); also `namespace`, `is_table`, `table_context`.
- `SectionBundle`: `visual_pdf_page_indices` -> `page_refs`.
- `ReferenceResolution`: positional `(status, ids, explanation)` and `request.originating_passage_id` -> contract `(request, status, explanation, candidate_passage_ids)` and `origin_passage_id`.

Only `_passage` and `_section` were patched locally for this check.
