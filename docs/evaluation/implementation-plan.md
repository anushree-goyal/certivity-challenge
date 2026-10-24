# Agent 3 implementation plan (quality, export, evaluation)

Status legend: DONE = implemented and unit-tested on synthetic fixtures with a scripted fake model.
Nothing here has run against the real corpus, real extraction, a real model, the official validator or the official scorer.

## Module layout (`src/certivity/quality/`, `src/certivity/evaluation/`)

| File | Purpose | Status |
| --- | --- | --- |
| `quality/_access.py` | Only place that reads contract fields (names ASSUMED until `contracts.py` lands) | DONE |
| `quality/_records.py` | Builds ReviewFinding/QualityReport/ValidationReport; temporary fallbacks if contracts missing | DONE |
| `quality/limits.py` | 2,000 chars / 5,000 requirements / 5 MB; assumed official field names | DONE |
| `quality/deterministic.py` | Source existence, base version, snapshot, printed page, text length, id uniqueness; numeric and lost-exception heuristics | DONE |
| `quality/review.py` | `review_requirements`: deterministic -> exact dups -> model review -> near dups; partial review is always reported | DONE |
| `quality/export.py` | `build_submission`, `build_submission_detailed` (trace + excluded reasons) | DONE |
| `quality/validate.py` | Own structural checks + subprocess wrapper for the official validator | DONE (wrapper CLI assumed) |
| `quality/prompts/review_prompt.py` | Review + dedup prompt text and output schemas (version `review-v1`) | DONE (untested against a real model) |
| `quality/semantic.py` | Batched review vs real evidence; verdicts cross-checked (6 checks + verbatim quote must occur in evidence); call budget; unreviewed IDs reported; page images for table-flagged passages | DONE (fake-model tests only) |
| `quality/dedup.py` | Exact duplicates (same text+primary passage+condition); near-duplicates need model confirmation plus equal numbers/conditions; same wording from a different passage is kept | DONE (fake-model tests only) |
| `evaluation/scoring.py` | `evaluate_submission`: own regulation/page P/R/F1, wrapped official scorer (CLI assumed), semantic/platform scores null | DONE |
| `evaluation/runs.py` + `scoring.classify_misses` | Candidate save/load/compare by content hash; 6 of the 9 miss categories as file-comparison candidates; the other 4 listed as manual-only | DONE (partial categories) |

## Decisions made now

- **Accepted != verified.** Until semantic review exists, `review_requirements` lists accepted IDs that passed deterministic checks and adds a `review_incomplete` remaining issue.
- **Reject vs revise vs flag.** Missing/wrong-version primary source: rejected. Missing page, no primary, over-length text, duplicate draft IDs: needs revision (returned as findings; never auto-corrected). Heuristic hits (number not in evidence, exception wording not carried): flagged for semantic review, never rejected on their own, so conditional rules are not erased.
- **No truncation.** Over-length text is excluded with a reason; >5,000 requirements or >5 MB raises `SubmissionBuildError`.
- **Export re-checks.** `build_submission` re-verifies the primary passage, base document and page itself rather than trusting the report.
- **`valid` means both layers passed**, including the official validator actually running.

## Still open

- Step 6 (connect to real extraction, improve the biggest failure categories) needs the real pipeline and corpus.
- Miss categories lost_reference, table_or_parse_failure, merged_obligation, incorrect_condition need run artifacts or manual labelling.
- Review prompts have never been run on a real model; expect to tune them on real drafts.
- Honest limits: heuristics (numbers, exception words) are regex-based; table-flag names and PageAsset hand-off to the vision adapter are assumed.

## Questions to resolve with other owners

1. Anushree: exact field names/types in `contracts.py` (edit `_access.py`/`_records.py` only); how `emit_event` takes a RunEvent; whether QualityReport has a field for "review incomplete".
2. Anushree: does `PipelineDraft` expose drafts as `draft_requirements`?
3. Teammate 2: is `list_documents(role="base")` the right way to tell base from latest, and does `get_passages` silently omit unknown IDs?
4. Anushree: what `images=` accepts in `generate_json` (I pass PageAsset objects from `get_page_asset`), and the passage field names for parsing flags / physical page indices (assumed `parsing_flags`, `pdf_page_indices`).
5. Participant package: exact `source` keys (assumed `regulation`, `version`, `page`, `paragraph`), whether `paragraph` is required/nullable, and the validator CLI (assumed `python tools/validate.py <file>`, non-zero exit on failure).

## Commands

    PYTHONPATH=src python3 -m unittest discover -s tests -t . -v

6. Participant package: `expected.json` layout (assumed `requirements[].source.{regulation,page}`) and the `score_local.py` CLI (assumed `python tools/score_local.py <submission> <expected>`).
