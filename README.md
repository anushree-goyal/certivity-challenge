# certivity-challenge

Turns a product specification and/or exploded-view drawing into the engineering requirements it must fulfil under UN Regulations. Each requirement cites the base-version page and paragraph. The output is the official hackathon submission JSON.

This app is built by four owners. The shared design is in the team plan (`certivity-main-goal-build-plan.md`, shared with each owner):

| Folder | Owner |
| --- | --- |
| `src/certivity/agent/`, `llm/`, `contracts.py` | Anushree: extraction, reasoning, shared contracts |
| `src/certivity/corpus/` | Teammate 2: regulation corpus, lookup, page rendering |
| `src/certivity/quality/`, `evaluation/` | Teammate 3: review, export, validation, scoring |
| `src/certivity/api/`, `run_store/`, `cli.py`, `web/`, root config | Teammate 4: API, runs, UI, integration |

## Setup

Requirements: Python 3.12+ and Node 20+ (for building the UI only).

```bash
git clone https://github.com/Certivity/industrial-ai-hackathon data/participant-package
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env                # set CERTIVITY_TEAM_ID (team id from hackathon.certivity.io) and model credentials
.venv/bin/certivity bootstrap --fetch   # first time: download base PDFs and build the corpus snapshot
(cd web && npm install && npm run build)
.venv/bin/certivity health          # shows what is ready; never prints secrets
.venv/bin/certivity serve           # rebuilds/reuses the snapshot, then http://127.0.0.1:8000 (API + UI)
```

If you keep the participant package somewhere else, set `CERTIVITY_PARTICIPANT_PACKAGE`. On macOS, if downloads fail with `CERTIFICATE_VERIFY_FAILED`, see the package README (`export SSL_CERT_FILE="$(python3 -m certifi)"`).

Frontend development with hot reload: run `certivity serve` and `cd web && npm run dev` together. Then open http://localhost:5173 (it proxies `/api`).

Headless run of one case: `certivity run --case dev-rear-lamp --team team-07`.

### Fixture mode

`CERTIVITY_PIPELINE_MODE=fixture`, or "Fixture" in the UI, runs labelled synthetic stand-ins (`src/certivity/api/fixture_mode.py`). You can use it before the real modules are merged. Fixture runs carry an orange **FIXTURE DATA** badge and banner. Their pages are placeholders, and their submissions still go through the official validator. They are never cached as real results.

## How a run works

`POST /api/runs` saves immutable input copies under `runs/<id>/input/` and queues the run. A single background worker then:

1. opens one corpus snapshot and the LLM adapter, and keeps them for the whole run;
2. calls `run_extraction`, then `review_requirements`;
3. while review findings request context or a correction, calls `revise_extraction` and reviews again, up to `CERTIVITY_MAX_REVISION_ROUNDS` and the time budget. Unfinished findings are reported;
4. calls `build_submission` and `validate_submission` (official validator). Only a valid file is offered for download;
5. stores evidence passages, then asks the evaluation module for local regulation/page scoring via `evaluate_case(submission_path, case_id, participant_package)`. The app never locates or reads the golden answer; the evaluation module does both, and returns nothing for a case without one.

Run states: `queued`, `running`, `completed` (valid output, full coverage, no open issues), `partial` (valid output but pending/failed coverage or open review issues), `failed`. A server restart marks unfinished runs `failed` with `interrupted: true`. A cached run (opt-in "reuse cached") reuses a fresh run with the same input, team/case, corpus snapshot, component versions and model settings, and is labelled "cached result".

Per-run folders: `agent/` (Anushree), `quality/` (Teammate 3), `app/` (app). The agent receives its folder as `artifacts_dir=`, and the model-call cache lives in `agent/llm-cache/`.

## HTTP API

| Route | Purpose |
| --- | --- |
| `POST /api/runs` | multipart: `team_id`, `case_id`, `package_case` or `spec_file`/`spec_text`, `drawings[]`, `mode`, `reuse_cached` |
| `GET /api/runs`, `GET /api/runs/{id}` | list; state, stage, progress, warnings, errors, recent events |
| `GET /api/runs/{id}/results` | RunView: profile, applicability, requirements + evidence, coverage, findings, validation, metrics |
| `GET /api/runs/{id}/submission` | the exact validated JSON, or 409 with the reason |
| `GET /api/runs/{id}/input/{name}` | the stored input files |
| `PUT /api/runs/{id}/platform-metrics` | record scores read off the organizer platform (never computed here) |
| `POST /api/runs/{id}/cancel` | queued runs stop at once; running runs stop when the current stage finishes |
| `GET /api/compare?a=&b=` | cited regulations/pages/paragraphs that differ between two runs (not a semantic comparison) |
| `GET /api/documents[/{id}/pdf \| /{id}/pages/{pdf_page_index}]` | source documents of the loaded snapshot; page index is zero-based; `?snapshot_id=` refuses a mismatched snapshot |
| `GET /api/cases`, `GET /api/health` | cases (re-read from disk; held-out first; sub-goal folders typed `subgoal`); readiness and corpus freshness |
| `GET /api/regulations` | every index entry: versions, PDF downloaded and verified, imported in the corpus, pages, document ID (no paths, no download URLs) |
| `POST/GET /api/corpus/reimport` | build a new corpus snapshot through the corpus module's `bootstrap`; refused while an analysis runs |

## Integration contract (what the app expects from each module)

Entry points are configurable (`CERTIVITY_EP_*` in `.env.example`). Defaults:

- **Corpus (confirmed by Teammate 2):** `bootstrap(package_dir, corpus_dir)` runs at `certivity serve` startup (no network; `certivity bootstrap --fetch` downloads missing PDFs). `open_corpus(corpus_dir)` opens the current snapshot for each run. Source pages open the run's own snapshot with `open_corpus(corpus_dir, snapshot_id=…)`, so they stay viewable after a re-import. `get_document_path`: `UnknownId` → 404, `FileNotFoundError` → 500. `PageAsset.asset_ref` is a local PNG. `/api/health` uses `corpus_status(corpus_dir)`.
- **LLM (confirmed, contracts 1.0.0):** `certivity.llm:create_llm(cache_dir=run/agent/llm-cache)`. `/api/health` shows `llm.describe()` as-is (secret-free). If `credentials_configured` is false, a real run fails at the loading stage with that reason, before any work.
- **Agent (confirmed):** `run_extraction(run_input, corpus, llm, emit_event, artifacts_dir=run/agent)`. The app builds the record with `contracts.make_run_input(run_id=, team_id=, case_id=, corpus_snapshot_id=, specification_text=, drawing_paths=, input_hash=)`, which is keyword-only and turns paths into `DrawingRef`s. Empty input is refused at upload, because `RunInput` rejects it.
- **Quality (confirmed by Teammate 3):** `review_requirements`, then `build_submission_detailed` (preferred; its `.submission`, `.export_mapping`, `.excluded`, `.blocking_issues` are stored in `app/export.json`), then `validate_submission(submission, participant_package)`. Drafts link to requirements through `export_mapping` only. A finding goes to revision when `requested_context` or `requested_correction` is set. A `review_incomplete` remaining issue makes the run `partial` and shows "not verified" in the UI. `SubmissionBuildError` (over 5,000 requirements or 5 MB) fails the run with that message. The quality module writes to `runs/<id>/quality/` and to `evaluation/` for candidate submissions.
- `emit_event` accepts a `RunEvent` record, a dict, or keyword arguments. It never raises into a module, so cancel takes effect when the current stage finishes.

`tests/integration/test_contract_conformance.py` checks these signatures as soon as a module is merged. Until then it is skipped. `tests/integration/test_real_mode_wiring.py` runs real mode against dataclass stand-ins.

## Integration checklist (after each component merge)

- [ ] `pytest` passes, with no conformance skips for merged modules; `certivity health` shows the merged module ready
- [ ] `certivity run --case dev-rear-lamp` reaches `completed` or `partial`, not `failed`
- [ ] the downloaded JSON passes `python data/participant-package/tools/validate.py <file>`
- [ ] one requirement's source page opens, and its printed page matches the citation
- [ ] an error path is visible in the UI (e.g. a real run with missing credentials)

## Submission (organizers)

- Held-out case: `git -C data/participant-package pull`, run `certivity bootstrap` again, then start a run with the new case from the dropdown. Set team ID and case ID (= the case folder name).
- Download the validated JSON from the run and upload it on the platform. For the held-out case the **last valid upload counts**, so keep the chosen file. Teammate 3 coordinates scoring uploads.
- Repository/demo: this repository plus `certivity serve`. No hosted services are required.

## Sunday held-out steps

The held-out case arrives as a new folder in the participant package. Nothing in the code needs to change.

1. Run `git pull` in the participant package (`git -C data/participant-package pull`).
2. Run `python tools/fetch_regulations.py --role base` in the participant package to download any new base PDFs.
3. Start or refresh the app (`certivity serve`, then http://localhost:8000). If the banner says "Regulation package changed — corpus needs re-import", click **Re-import corpus** and wait for it to finish. The **Regulations** page shows what is downloaded and imported.
4. Make sure `CERTIVITY_TEAM_ID` in `.env` is your real team id (the app warns if it is missing or a placeholder). Under "Case from the regulation package", select **heldout-front-bumper**: it is listed first, and its folder name becomes the case ID. Check the inputs the form says it picked up, then start the run.
5. On the run page, check the case ID and validator status beside **Export requirements JSON**, then export and upload at hackathon.certivity.io. The last valid upload counts, not the best; at most 5 valid uploads per hour; uploads close at 13:00.

New case folders, new index entries and newly downloaded PDFs are read from disk on each request, so the app does not need a restart. Re-importing builds a new corpus snapshot. Analyses already finished keep showing their own snapshot, and re-import is disabled while an analysis is running.

## Third-party licences

- **PyMuPDF** (PDF parsing and page rendering, corpus module) is **AGPL-3.0** (or a commercial Artifex licence). The team accepted it for the hackathon on 2026-10-24: the repository is private and organizers do not run our code. If this application is later distributed or offered to other people over a network, its complete source must be made available under the AGPL, or PyMuPDF replaced or commercially licensed.
- The participant package's tools and schemas are MIT; its data is for use in this hackathon only (`data/participant-package/LICENSE-DATA.md`). The package and downloaded PDFs are not committed.
