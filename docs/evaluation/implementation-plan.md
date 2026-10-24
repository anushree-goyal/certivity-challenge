# Agent 3 implementation plan (quality, export, evaluation)

Status legend: DONE = implemented and unit-tested on synthetic fixtures with a scripted fake model.
The official validator and local scorer (participant package commit 2104a8e) now run in the test suite
when the package is present. Nothing here has run against the real corpus, real extraction or a real model.

## Module layout (`src/certivity/quality/`, `src/certivity/evaluation/`)

| File | Purpose | Status |
| --- | --- | --- |
| `quality/_access.py` | Only place that reads contract fields (names ASSUMED until `contracts.py` lands) | DONE |
| `quality/_records.py` | Builds ReviewFinding/QualityReport/ValidationReport; temporary fallbacks if contracts missing | DONE |
| `quality/limits.py` | 2,000 chars / 5,000 requirements / 5 MB; assumed official field names | DONE |
| `quality/deterministic.py` | Source existence, base version, snapshot, printed page, text length, id uniqueness; numeric and lost-exception heuristics | DONE |
| `quality/review.py` | `review_requirements`: deterministic -> exact dups -> model review -> near dups; partial review is always reported | DONE |
| `quality/export.py` | `build_submission`, `build_submission_detailed` (trace + excluded reasons) | DONE |
| `quality/validate.py` | Own structural checks + subprocess wrapper for the official validator | DONE (CLI checked; real-validator tests) |
| `quality/prompts/review_prompt.py` | Review + dedup prompt text and output schemas (version `review-v1`) | DONE (untested against a real model) |
| `quality/semantic.py` | Batched review vs real evidence; verdicts cross-checked (6 checks + verbatim quote must occur in evidence); call budget; unreviewed IDs reported; page images for table-flagged passages | DONE (fake-model tests only) |
| `quality/dedup.py` | Exact duplicates (same text+primary passage+condition); near-duplicates need model confirmation plus equal numbers/conditions; same wording from a different passage is kept | DONE (fake-model tests only) |
| `evaluation/scoring.py` | `evaluate_submission`: own regulation and (regulation, version, page) P/R/F1, wrapped official scorer, semantic/platform scores null | DONE (CLI checked; real-scorer tests) |
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

## Contract changes requested from Anushree (she owns contracts.py)

- `QualityReport.export_mapping: dict[str, str]` (draft ID -> requirement ID), default `{}`. Filled by `build_submission` with what was actually exported (empty before export, and for drafts excluded at export). Until the field exists, `BuildResult.export_mapping` carries it and a warning is recorded.
- `ReviewFinding.requested_context: str | None` and `ReviewFinding.requested_correction: str | None`. Every finding that needs revision/context sets at least one (enforced by tests): context = more source needed, correction = what must change in the draft.

## Questions to resolve with other owners

1. Anushree: exact field names/types in `contracts.py` (edit `_access.py`/`_records.py` only); whether QualityReport has a field for "review incomplete". Partly answered (contracts 1.0.0, branch `feature/agent`, not yet pushed): `RunInput.team_id`, `case_id`, `corpus_snapshot_id` match `_access.py`; `emit_event` takes one `RunEvent` (`_records.emit` now builds one from the fields it declares, else sends a dict); the adapter raises `LLMConfigurationError` without a credential (semantic review now stops after the first such error and marks every remaining draft unreviewed). Still unseen: `RunEvent`, `DraftRequirement`, `SourcePassage`, `QualityReport`, `ReviewFinding` field names.
2. Anushree: does `PipelineDraft` expose drafts as `draft_requirements`?
3. Teammate 2: is `list_documents(role="base")` the right way to tell base from latest, and does `get_passages` silently omit unknown IDs?
4. Anushree: what `images=` accepts in `generate_json` (I pass PageAsset objects from `get_page_asset`), and the passage field names for parsing flags / physical page indices (assumed `parsing_flags`, `pdf_page_indices`).
5. ~~Participant package: source keys, paragraph, validator CLI.~~ RESOLVED, see "Checked against the participant package".

## Commands

    PYTHONPATH=src python3 -m unittest discover -s tests -t . -v
    # with the real validator/scorer:
    CERTIVITY_PARTICIPANT_PACKAGE=/path/to/industrial-ai-hackathon PYTHONPATH=src python3 -m unittest discover -s tests -t . -v

6. ~~Participant package: expected.json layout and scorer CLI.~~ RESOLVED (the scorer CLI guess was wrong and is fixed).

## Checked against the participant package (commit 2104a8e)

- Schema: `team_id`, `case_id`, `requirements[]` with `id`, `text` (non-blank, <= 2,000), `source.{regulation, version, page >= 1}`, optional `source.paragraph` (string; `null` is rejected, so we omit it), optional `test_case`. Extra fields are allowed unless `--strict`.
- `tools/validate.py <file> [--index ...]`: exit 0 valid, 1 problems, 2 unreadable. Needs Python 3.12+ and `jsonschema`. It checks regulation names and that the version is *some* version in the index. **It accepts latest-version labels** (e.g. UN R10 7.3.0), so base-version enforcement stays our job (export re-check + deterministic rejection; tested).
- `tools/score_local.py <submission> --expected <file> --json` (the old wrapper passed expected as a second positional argument, which argparse rejects). Page level keys on (regulation, version, page) and also credits a page when a cited paragraph matches. Our own page metric now includes the version too; it counts exact matches only, so quote the official numbers when it ran.
- `expected.json` (dev rear lamp): 190 requirements; every source has a paragraph string. Read only by `evaluation/` and the evaluation tests.

Tests: `tests/quality/test_official_package.py`, `tests/evaluation/test_official_scorer.py`. They skip unless `CERTIVITY_PARTICIPANT_PACKAGE` points at the package or it is at `data/participant-package/`. Drafts and corpus are still synthetic; only the labels are real.
