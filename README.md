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
cp .env.example .env                # set CERTIVITY_TEAM_ID and model credentials
.venv/bin/certivity bootstrap       # download + import the base corpus (corpus module)
(cd web && npm install && npm run build)
.venv/bin/certivity health          # shows what is ready; never prints secrets
.venv/bin/certivity serve           # http://127.0.0.1:8000 (API + built UI)
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
5. stores evidence passages and runs local regulation/page scoring if the case has an `expected.json`. Only the evaluation module reads that file, after runtime processing ends.

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
| `GET /api/cases`, `GET /api/health` | participant-package cases; readiness |

## Integration contract (what the app expects from each module)

Entry points are configurable (`CERTIVITY_EP_*` in `.env.example`). Defaults:

- **Corpus:** `certivity.corpus:open_corpus(corpus_dir) -> CorpusReader`, which exposes a `snapshot_id` attribute and the planned methods. For the PDF route, provide `get_document_path(document_id) -> Path`. A `PageAsset` provides `content` bytes, or a local file path in `asset_path`. Optional: `certivity.corpus:bootstrap(package_dir, corpus_dir)`.
- **LLM (confirmed, contracts 1.0.0):** `certivity.llm:create_llm(cache_dir=run/agent/llm-cache)`. `/api/health` shows `llm.describe()` as-is (secret-free). If `credentials_configured` is false, a real run fails at the loading stage with that reason, before any work.
- **Agent (confirmed):** `run_extraction(run_input, corpus, llm, emit_event, artifacts_dir=run/agent)`. The app builds the record with `contracts.make_run_input(run_id=, team_id=, case_id=, corpus_snapshot_id=, specification_text=, drawing_paths=, input_hash=)`, which is keyword-only and turns paths into `DrawingRef`s. Empty input is refused at upload, because `RunInput` rejects it.
- **Quality:** a finding triggers revision when any `requested_*` field is set, unless the report has `needs_revision: false`. Please add `export_mapping: {draft_id: requirement_id}` to `QualityReport`. Without it, the app infers the mapping from export order and shows a warning.
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
