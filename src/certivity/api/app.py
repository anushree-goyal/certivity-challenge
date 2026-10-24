"""FastAPI application: the HTTP boundary between the browser and the pipeline components.

Handlers validate input, manage run lifecycle and serve persisted artifacts and source pages.
They contain no applicability, extraction, review or export logic.
"""

from __future__ import annotations

import hashlib
import json
import re
import mimetypes
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from certivity import __version__
from certivity.api.components import (
    ComponentError, component_versions, model_configuration, open_corpus_only, package_case, readiness,
    snapshot_id_of,
)
from certivity.api.pipeline import build_run_view
from certivity.api.serialization import first, to_jsonable
from certivity.api.settings import REPO_ROOT, Settings, load_settings
from certivity.api.worker import RunWorker
from certivity.run_store import RunState, RunStore
from certivity.run_store.store import now_iso

ENGINEER_REVIEW_STATES = ("unreviewed", "approved", "flagged")
CASE_ID_PATTERN = re.compile(r"^[a-z0-9-]+$")
MAX_SPEC_BYTES = 2 * 1024 * 1024
MAX_DRAWING_BYTES = 25 * 1024 * 1024
DRAWING_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
SPEC_TYPES = (".md", ".markdown", ".txt")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _field(obj: Any, *names: str) -> Any:
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value is not None:
            return value
    return None


def create_app(settings: Settings | None = None, start_worker: bool = True) -> FastAPI:
    settings = settings or load_settings()
    store = RunStore(settings.runs_dir)
    worker = RunWorker(store, settings)
    local = threading.local()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        interrupted = store.mark_interrupted()
        app.state.interrupted_on_start = interrupted
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Certivity requirements", version=__version__, lifespan=lifespan)
    app.state.settings, app.state.store, app.state.worker = settings, store, worker

    def corpus_for(mode: str, snapshot_id: str | None = None):
        """Read-only corpus, one per HTTP thread and mode (SQLite connections are thread-bound).

        With snapshot_id, opens that pinned snapshot, so a run's sources stay viewable after a re-import.
        Without it, opens the current snapshot.
        """
        cache = getattr(local, "corpora", None)
        if cache is None:
            cache = local.corpora = {}
        key = (mode, snapshot_id)
        if key not in cache:
            try:
                cache[key] = open_corpus_only(settings, mode, snapshot_id)
            except ComponentError as exc:
                raise HTTPException(503, f"Corpus unavailable in {mode} mode: {exc}") from exc
            except Exception as exc:
                raise HTTPException(503, f"Corpus failed to open: {type(exc).__name__}: {exc}") from exc
        return cache[key]

    def get_manifest(run_id: str) -> dict[str, Any]:
        try:
            return store.read(run_id)
        except KeyError:
            raise HTTPException(404, f"Run {run_id!r} not found") from None

    # --- health and cases ------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"app": {"ok": True, "version": __version__, "active_run": worker.active_run,
                        "queued_runs": worker.queued}, **readiness(settings)}

    @app.get("/api/cases")
    def cases() -> list[dict[str, Any]]:
        out = []
        if settings.cases_dir.is_dir():
            for case_dir in sorted(p for p in settings.cases_dir.iterdir() if p.is_dir()):
                case = package_case(settings, case_dir.name)
                out.append({"case_id": case["case_id"], "has_spec": case["spec_path"] is not None,
                            "drawings": [p.name for p in case["drawing_paths"]]})
        return out

    # --- runs ------------------------------------------------------------------------------

    @app.post("/api/runs", status_code=202)
    async def create_run(
        team_id: str = Form(""),
        case_id: str = Form(""),
        package_case_id: str = Form("", alias="package_case"),
        spec_text: str = Form(""),
        mode: str = Form(""),
        reuse_cached: bool = Form(False),
        spec_file: UploadFile | None = File(None),
        drawings: list[UploadFile] = File(default=[]),
    ) -> dict[str, Any]:
        mode = (mode or settings.pipeline_mode).lower()
        if mode not in ("real", "fixture"):
            raise HTTPException(422, "mode must be 'real' or 'fixture'")
        files: dict[str, bytes] = {}
        spec_bytes: bytes | None = None
        drawing_files: list[str] = []

        if package_case_id:
            try:
                case = package_case(settings, package_case_id)
            except KeyError:
                raise HTTPException(422, f"Case {package_case_id!r} is not in the participant package") from None
            if case["spec_path"]:
                spec_bytes = case["spec_path"].read_bytes()
            for p in case["drawing_paths"]:
                name = f"drawing-{len(drawing_files) + 1}{p.suffix.lower()}"
                files[name] = p.read_bytes()
                drawing_files.append(name)
            case_id = case_id or package_case_id

        if spec_file is not None and spec_file.filename:
            if Path(spec_file.filename).suffix.lower() not in SPEC_TYPES:
                raise HTTPException(422, f"Specification must be one of {', '.join(SPEC_TYPES)}")
            spec_bytes = await spec_file.read()
        elif spec_text.strip():
            spec_bytes = spec_text.encode("utf-8")
        if spec_bytes is not None and not spec_bytes.strip():
            spec_bytes = None  # an empty file is no specification
        if spec_bytes is not None:
            if len(spec_bytes) > MAX_SPEC_BYTES:
                raise HTTPException(413, "Specification is larger than 2 MB")
            try:
                spec_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(422, "Specification must be UTF-8 text") from None
            files["spec.md"] = spec_bytes

        for upload in drawings:
            if not upload.filename:
                continue
            suffix = Path(upload.filename).suffix.lower()
            if suffix not in DRAWING_TYPES:
                raise HTTPException(422, f"Drawing {upload.filename!r} must be PNG, JPEG or WebP")
            content = await upload.read()
            if len(content) > MAX_DRAWING_BYTES:
                raise HTTPException(413, f"Drawing {upload.filename!r} is larger than 25 MB")
            name = f"drawing-{len(drawing_files) + 1}{suffix}"
            files[name] = content
            drawing_files.append(name)

        if "spec.md" not in files and not drawing_files:
            raise HTTPException(422, "Provide a specification and/or an exploded-view drawing")
        team_id = team_id.strip() or settings.default_team_id.strip()
        case_id = case_id.strip()
        if not team_id or not case_id:
            raise HTTPException(422, "team_id and case_id are required for the submission")
        is_package_folder = settings.cases_dir.is_dir() and (settings.cases_dir / case_id).is_dir() and "/" not in case_id
        if not is_package_folder and not CASE_ID_PATTERN.match(case_id):
            raise HTTPException(422, "case_id may contain only lowercase letters, digits and hyphens")

        input_hash = _sha256(json.dumps(sorted((n, _sha256(c)) for n, c in files.items())).encode())
        snapshot_id = None
        try:
            snapshot_id = snapshot_id_of(corpus_for(mode))
        except HTTPException:
            pass  # the run itself will fail with the full reason
        cache_key = _sha256(json.dumps({
            "input_hash": input_hash, "team_id": team_id, "case_id": case_id, "mode": mode,
            "snapshot_id": snapshot_id, "entry_points": settings.entry_points,
            "versions": component_versions(settings, mode), "model": model_configuration(),
            "max_revision_rounds": settings.max_revision_rounds,
        }, sort_keys=True).encode())

        base = {"team_id": team_id, "case_id": case_id, "package_case": package_case_id or None, "mode": mode,
                "input_hash": input_hash, "cache_key": cache_key, "corpus_snapshot_id": snapshot_id,
                "spec_file": "spec.md" if "spec.md" in files else None, "drawing_files": drawing_files}

        if reuse_cached and snapshot_id is not None and (cached := store.find_cached(cache_key)):
            manifest = store.create({**base, "origin": "cached", "cached_from": cached["run_id"]}, files)
            store.update(manifest["run_id"], state=cached["state"], stage="done", started_at=cached.get("started_at"),
                         finished_at=cached.get("finished_at"), submission_valid=cached.get("submission_valid"),
                         revision_rounds=cached.get("revision_rounds"), llm_identity=cached.get("llm_identity"),
                         warnings=[f"Cached result reused from run {cached['run_id']} (same input and configuration).",
                                   *cached.get("warnings", [])])
            return {"run_id": manifest["run_id"], "state": cached["state"], "origin": "cached",
                    "cached_from": cached["run_id"]}

        manifest = store.create({**base, "origin": "fixture" if mode == "fixture" else "fresh"}, files)
        worker.submit(manifest["run_id"])
        return {"run_id": manifest["run_id"], "state": manifest["state"], "origin": manifest["origin"]}

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        keys = ("run_id", "state", "stage", "origin", "mode", "team_id", "case_id", "created_at", "finished_at",
                "submission_valid", "cached_from", "interrupted")
        return [{k: m.get(k) for k in keys} for m in store.list_runs()]

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str) -> dict[str, Any]:
        m = get_manifest(run_id)
        return {k: m.get(k) for k in (
            "run_id", "state", "stage", "origin", "mode", "cached_from", "team_id", "case_id", "progress",
            "warnings", "errors", "interrupted", "created_at", "started_at", "finished_at", "revision_rounds",
            "submission_valid", "corpus_snapshot_id", "cancel_requested", "cancelled")} | {
            "finished": RunState(m["state"]).finished,
            "events": store.read_events(m.get("cached_from") or run_id, limit=100)}

    @app.get("/api/runs/{run_id}/results")
    def run_results(run_id: str) -> dict[str, Any]:
        get_manifest(run_id)
        view = build_run_view(store, run_id)
        if view is None:
            raise HTTPException(409, "No results yet: extraction has not produced a draft for this run")
        return view

    @app.get("/api/runs/{run_id}/submission")
    def run_submission(run_id: str):
        m = get_manifest(run_id)
        path = store.artifact_path(m.get("cached_from") or run_id, "submission.json")
        if not path.is_file():
            if not RunState(m["state"]).finished:
                reason = f"The run is still {m['state']} (stage: {m.get('stage')})."
            elif m["state"] == RunState.FAILED.value:
                reason = "The run failed: " + ("; ".join(e.get("message", "") for e in m.get("errors", [])) or "see run errors.")
            else:
                reason = "The submission did not pass the official validator; see validation errors in the results."
            return JSONResponse({"available": False, "reason": reason}, status_code=409)
        safe = lambda v: re.sub(r"[^A-Za-z0-9._-]+", "-", str(v or "")).strip("-") or "unknown"
        return FileResponse(path, media_type="application/json",
                            filename=f"{safe(m['case_id'])}_{safe(m['team_id'])}_{run_id}.json")

    @app.get("/api/runs/{run_id}/input/{name}")
    def run_input_file(run_id: str, name: str):
        get_manifest(run_id)
        path = store.run_dir(run_id) / "input" / Path(name).name
        if not path.is_file():
            raise HTTPException(404, "No such input file")
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "text/markdown")

    @app.put("/api/runs/{run_id}/platform-metrics")
    async def put_platform_metrics(run_id: str, request: Request) -> dict[str, Any]:
        """Scores read off the organizer platform, entered by hand. Never computed here."""
        get_manifest(run_id)
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(422, "Expected a JSON object")
        store.write_artifact(run_id, "platform_metrics.json", {**data, "source": "entered manually from the platform"})
        return store.read_artifact(run_id, "platform_metrics.json")

    @app.put("/api/runs/{run_id}/reviews/{draft_id:path}")
    async def put_engineer_review(run_id: str, draft_id: str, request: Request) -> dict[str, Any]:
        """An engineer's own review of one requirement. Recorded with the run; never written into the submission."""
        m = get_manifest(run_id)
        draft = store.read_artifact(m.get("cached_from") or run_id, "pipeline_draft.json") or {}
        if draft_id not in {d.get("draft_id") for d in draft.get("draft_requirements") or []}:
            raise HTTPException(404, f"No draft {draft_id!r} in this run")
        body = await request.json()
        status = body.get("status") if isinstance(body, dict) else None
        if status not in ENGINEER_REVIEW_STATES:
            raise HTTPException(422, f"status must be one of {', '.join(ENGINEER_REVIEW_STATES)}")
        note = str(body.get("note") or "")[:2000]
        entry = {"status": status, "note": note, "updated_at": now_iso()}

        def change(reviews):
            reviews = dict(reviews or {})
            if status == "unreviewed" and not note:
                reviews.pop(draft_id, None)
            else:
                reviews[draft_id] = entry
            return reviews

        store.update_artifact(run_id, "engineer_reviews.json", change)
        return {"draft_id": draft_id, **entry}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, Any]:
        """Queued runs stop at once. Running runs stop at the next stage boundary: the current stage
        (e.g. extraction) finishes first."""
        m = get_manifest(run_id)
        state = RunState(m["state"])
        if state.finished:
            raise HTTPException(409, f"Run is already {state.value}")
        if state == RunState.QUEUED:
            store.update(run_id, state=RunState.FAILED.value, cancelled=True, cancel_requested=True, finished_at=now_iso(),
                         errors=[*m.get("errors", []), {"stage": "queued", "message": "Cancelled by user before it started."}])
            return {"run_id": run_id, "state": RunState.FAILED.value, "cancelled": True}
        store.update(run_id, cancel_requested=True)
        return {"run_id": run_id, "state": state.value, "cancel_requested": True}

    @app.get("/api/compare")
    def compare_runs(a: str, b: str) -> dict[str, Any]:
        """Citation-level difference between two runs' outputs. Not a semantic comparison."""
        sides = {}
        for key, run_id in (("a", a), ("b", b)):
            m = get_manifest(run_id)
            source = m.get("cached_from") or run_id
            path = store.artifact_path(source, "submission.json")
            sub = (json.loads(path.read_text(encoding="utf-8")) if path.is_file()
                   else store.read_artifact(source, "submission.invalid.json")) or {"requirements": []}
            reqs = sub.get("requirements") or []
            sides[key] = {
                "run": {k: m.get(k) for k in ("run_id", "state", "origin", "case_id", "team_id", "created_at", "submission_valid")},
                "requirements": len(reqs),
                "regulations": {r["source"]["regulation"] for r in reqs},
                "pages": {(r["source"]["regulation"], r["source"]["version"], r["source"]["page"]) for r in reqs},
                "paragraphs": {(r["source"]["regulation"], r["source"].get("paragraph")) for r in reqs
                               if r["source"].get("paragraph")},
                "evaluation": store.read_artifact(source, "evaluation.json"),
            }

        def diff(field: str) -> dict[str, Any]:
            sa, sb = sides["a"][field], sides["b"][field]
            fmt = (lambda x: x) if field == "regulations" else (lambda t: " ".join(str(v) for v in t))
            return {"both": len(sa & sb), "only_a": sorted(fmt(x) for x in sa - sb), "only_b": sorted(fmt(x) for x in sb - sa)}

        return {
            "a": {k: v for k, v in sides["a"].items() if k in ("run", "requirements", "evaluation")},
            "b": {k: v for k, v in sides["b"].items() if k in ("run", "requirements", "evaluation")},
            "regulations": diff("regulations"), "pages": diff("pages"), "paragraphs": diff("paragraphs"),
            "note": "Compares cited regulations, pages and paragraphs only; requirement meaning is not compared.",
        }

    # --- source documents ------------------------------------------------------------------
    # IDs are opaque corpus identifiers (e.g. "un-r48:9-3-0:sha256-…"). Clients URL-encode them; the :path
    # converter also accepts IDs containing "/", which the server decodes before routing.

    def check_snapshot(corpus, snapshot_id: str | None) -> None:
        current = snapshot_id_of(corpus)
        if snapshot_id and current and snapshot_id != current:
            raise HTTPException(409, f"Run used corpus snapshot {snapshot_id}, but {current} is loaded. "
                                     "Its source pages are not served from a different snapshot.")

    @app.get("/api/documents")
    def documents(mode: str = "", snapshot_id: str | None = None) -> list[dict[str, Any]]:
        corpus = corpus_for(mode or settings.pipeline_mode, snapshot_id)
        check_snapshot(corpus, snapshot_id)
        hidden = {"file_path", "path", "local_path"}  # internal paths stay server-side
        return [{k: v for k, v in to_jsonable(d).items() if k not in hidden} for d in corpus.list_documents(role="base")]

    @app.get("/api/documents/{document_id:path}/pdf")
    def document_pdf(document_id: str, mode: str = "", snapshot_id: str | None = None):
        corpus = corpus_for(mode or settings.pipeline_mode, snapshot_id)
        check_snapshot(corpus, snapshot_id)
        path = None
        if hasattr(corpus, "get_document_path"):
            try:
                path = corpus.get_document_path(document_id)
            except KeyError:  # corpus UnknownId
                raise HTTPException(404, f"Unknown document {document_id!r} in this snapshot") from None
            except FileNotFoundError as exc:
                raise HTTPException(500, f"Source PDF for {document_id!r} is missing or changed on disk: {exc}") from None
        else:
            doc = next((d for d in corpus.list_documents(role="base") if _field(d, "document_id") == document_id), None)
            path = _field(doc, "file_path", "path", "local_path") if doc is not None else None
        if not path or not Path(path).is_file():
            raise HTTPException(404, f"No original PDF available for {document_id!r}")
        return FileResponse(path, media_type="application/pdf", content_disposition_type="inline",
                            filename=Path(path).name)

    @app.get("/api/documents/{document_id:path}/pages/{pdf_page_index}")
    def document_page(document_id: str, pdf_page_index: int, mode: str = "", snapshot_id: str | None = None):
        if pdf_page_index < 0:
            raise HTTPException(422, "pdf_page_index is zero-based and must be >= 0")
        corpus = corpus_for(mode or settings.pipeline_mode, snapshot_id)
        check_snapshot(corpus, snapshot_id)
        try:
            asset = corpus.get_page_asset(document_id, pdf_page_index)
        except (KeyError, IndexError, ValueError) as exc:
            raise HTTPException(404, f"No page {pdf_page_index} for {document_id!r}: {exc}") from None
        media_type = _field(asset, "media_type") or "image/png"
        headers = {"X-Pdf-Page-Index": str(pdf_page_index), "Cache-Control": "max-age=3600"}
        printed = _field(asset, "printed_page", "printed_page_label")
        if printed is not None:
            headers["X-Printed-Page-Label"] = str(printed)
        content = _field(asset, "content", "data")
        if isinstance(content, (bytes, bytearray)):
            return Response(bytes(content), media_type=media_type, headers=headers)
        path = _field(asset, "asset_path", "asset_ref", "local_path", "path", "file_path")
        if not path or not Path(path).is_file():
            raise HTTPException(500, "Corpus returned a page asset without content or a readable file")
        return FileResponse(path, media_type=media_type, headers=headers)

    # --- built frontend --------------------------------------------------------------------

    dist = REPO_ROOT / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")

    return app
