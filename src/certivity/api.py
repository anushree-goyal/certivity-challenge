"""HTTP API for the web UI (web/), and the built UI itself."""

from __future__ import annotations

import json
import re
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from certivity import __version__, checks, corpus, pipeline
from certivity.config import settings
from certivity.llm import credentials_configured
from certivity.runs import RunStore, now

SPEC_SUFFIXES = {".md", ".markdown", ".txt"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

store = RunStore()
reimport = {"state": "idle"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.mark_interrupted()
    store.start_worker()
    yield


app = FastAPI(title="certivity", lifespan=lifespan)


def _run(run_id: str) -> dict:
    try:
        return store.get(run_id)
    except KeyError:
        raise HTTPException(404, f"No run {run_id}")


# --- package, corpus, health -------------------------------------------------------------------


def list_cases() -> list[dict]:
    out = []
    for kind, folder in (("main", "cases"), ("subgoal", "subgoal")):
        root = settings.package_dir / folder
        for d in sorted(root.iterdir() if root.is_dir() else []):
            if not d.is_dir():
                continue
            specs = sorted(f.name for f in d.iterdir() if f.suffix.lower() in SPEC_SUFFIXES)
            drawings = sorted(f.name for f in d.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES)
            spec = next((s for s in specs if s.startswith("spec")), specs[0] if specs else None)
            out.append({"case_id": d.name if kind == "main" else f"subgoal-{d.name}", "type": kind,
                        "held_out": not d.name.startswith("dev"), "spec_file": spec, "spec_files": specs,
                        "drawings": drawings, "has_spec": spec is not None})
    return sorted(out, key=lambda c: (not c["held_out"], c["case_id"]))


@lru_cache(maxsize=None)
def _verified(path: str, mtime: float, sha256: str | None) -> bool:
    doc = corpus.Document("", "", "", Path(path), sha256)
    return corpus.pdf_verified(doc)


def regulation_rows() -> list[dict]:
    docs = corpus.documents()
    rows = []
    for entry in corpus.load_index():
        latest = next((v["version_label"] for v in entry["versions"] if v["role"] == "latest"), None)
        doc = docs.get(entry["regulation"])
        ok = bool(doc and doc.pdf and _verified(str(doc.pdf), doc.pdf.stat().st_mtime, doc.sha256))
        pdf = "verified" if ok else "checksum mismatch" if doc and doc.pdf else "missing"
        rows.append({
            "regulation": entry["regulation"], "title": entry.get("title"),
            "base_version": doc.version if doc else None, "latest_version": latest, "pdf": pdf,
            "downloaded": bool(doc and doc.pdf), "verified": ok, "in_corpus": ok,
            "page_count": corpus.page_count(doc) if ok else None, "document_id": doc.document_id if ok else None,
            "source_url": entry.get("source_url"),
            "files": [{"name": doc.pdf.name if doc and doc.pdf else "", "present": bool(doc and doc.pdf), "verified": ok}],
        })
    return rows


@app.get("/api/cases")
def cases() -> list[dict]:
    return list_cases()


@app.get("/api/regulations")
def regulations() -> dict:
    rows = regulation_rows()
    imported = sum(r["in_corpus"] for r in rows)
    return {"summary": {"in_index": len(rows), "downloaded": sum(r["downloaded"] for r in rows),
                        "imported": imported, "corpus_ready": imported > 0},
            "regulations": rows}


@app.get("/api/health")
def health() -> dict:
    package_ready = (settings.package_dir / "regulations" / "index.json").is_file()
    rows = regulation_rows() if package_ready else []
    verified = sum(r["verified"] for r in rows)
    llm_ready = credentials_configured()
    placeholder = settings.team_id in {"team-07", "team-xx"}
    return {
        "app": {"ok": True, "version": __version__, "active_run": store.active, "queued_runs": store.queue.qsize()},
        "team_id": {"configured": bool(settings.team_id),
                    "warning": f"CERTIVITY_TEAM_ID is {settings.team_id!r}, the README's example" if placeholder else None},
        "pipeline_mode": "real",
        "participant_package": {"ready": package_ready, "cases": [c["case_id"] for c in list_cases()] if package_ready else []},
        "modules": {},
        "corpus": {"ready": verified > 0, "snapshot_id": None, "base_documents": verified},
        "llm": {"ready": llm_ready, "identity": f"{settings.model_select} + {settings.model_filter}" if llm_ready else None,
                "error": None if llm_ready else "ANTHROPIC_API_KEY is not set"},
        "real_pipeline_ready": package_ready and verified > 0 and llm_ready,
        "corpus_freshness": {
            "needs_import": False, "reasons": [], "not_imported": [], "no_longer_listed": [],
            "missing_pdfs": [r["regulation"] for r in rows if r["pdf"] == "missing"],
            "checksum_mismatch": [r["regulation"] for r in rows if r["pdf"] == "checksum mismatch"],
            "index_entries": len(rows), "verified_pdfs": verified,
        },
        "corpus_reimport": reimport,
    }


@app.get("/api/corpus/reimport")
def reimport_status() -> dict:
    return reimport


@app.post("/api/corpus/reimport")
def start_reimport() -> dict:
    """Parse every base PDF again (after the package was updated)."""
    if reimport["state"] == "running":
        return reimport

    def work() -> None:
        try:
            corpus.paragraphs.cache_clear()
            for path in (settings.cache_dir / "paragraphs").glob("*.json"):
                path.unlink()
            for regulation in corpus.documents():
                corpus.paragraphs(regulation)
            reimport.update(state="done", finished_at=now())
        except Exception as exc:
            reimport.update(state="failed", finished_at=now(), error=str(exc))

    reimport.clear()
    reimport.update(state="running", started_at=now())
    threading.Thread(target=work, daemon=True).start()
    return reimport


@app.get("/api/documents/{document_id}/pdf")
def document_pdf(document_id: str) -> FileResponse:
    doc = corpus.document_by_id(document_id)
    if not doc or not doc.pdf:
        raise HTTPException(404, f"No document {document_id}")
    return FileResponse(doc.pdf, media_type="application/pdf")


@app.get("/api/documents/{document_id}/pages/{pdf_page_index}")
def document_page(document_id: str, pdf_page_index: int) -> Response:
    doc = corpus.document_by_id(document_id)
    if not doc or not doc.pdf or not 0 <= pdf_page_index < (corpus.page_count(doc) or 0):
        raise HTTPException(404, "No such page")
    return Response(corpus.render_page(doc, pdf_page_index), media_type="image/png")


# --- runs ----------------------------------------------------------------------------------------


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-") or "custom"


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name) or "file"


@app.post("/api/runs")
async def create_run(
    package_case: str = Form(""), spec_text: str = Form(""), case_id: str = Form(""),
    spec_file: UploadFile | None = File(None), drawings: list[UploadFile] = File(default_factory=list),
    mode: str = Form("real"), reuse_cached: str = Form("false"), dev: str = Form("false"),
) -> dict:
    spec: tuple[str, bytes] | None = None
    images: list[tuple[str, bytes]] = []
    if package_case:
        case = next((c for c in list_cases() if c["case_id"] == package_case and c["type"] == "main"), None)
        if not case:
            raise HTTPException(400, f"Unknown case {package_case}")
        folder = settings.package_dir / "cases" / package_case
        if case["spec_file"]:
            spec = (case["spec_file"], (folder / case["spec_file"]).read_bytes())
        images = [(d, (folder / d).read_bytes()) for d in case["drawings"]]
    if spec_file and spec_file.filename:
        spec = (_safe_name(spec_file.filename), await spec_file.read())
    elif spec_text.strip() and not spec:
        spec = ("spec.md", spec_text.encode())
    for d in drawings:
        if d.filename:
            if Path(d.filename).suffix.lower() not in IMAGE_SUFFIXES:
                raise HTTPException(400, f"{d.filename}: drawings must be PNG, JPEG or WebP")
            images.append((_safe_name(d.filename), await d.read()))
    if not spec and not images:
        raise HTTPException(400, "Add a specification or a drawing")
    case_id = case_id or package_case or (_slug(spec_file.filename) if spec_file and spec_file.filename else "custom")
    run = store.create(case_id=case_id, spec=spec, drawings=images)
    return {"run_id": run["run_id"], "origin": "fresh"}


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return [{k: r.get(k) for k in ("run_id", "state", "stage", "origin", "mode", "team_id", "case_id", "created_at",
                                    "finished_at", "submission_valid", "interrupted")} for r in store.list()]


@app.get("/api/runs/{run_id}")
def run_status(run_id: str) -> dict:
    run = _run(run_id)
    return {**run, "events": run["events"][-200:]}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict:
    _run(run_id)
    return store.cancel(run_id)


class ReviewBody(BaseModel):
    status: str
    note: str = ""


@app.put("/api/runs/{run_id}/reviews/{draft_id}")
def put_review(run_id: str, draft_id: str, body: ReviewBody) -> dict:
    _run(run_id)
    if body.status not in {"unreviewed", "approved", "flagged"}:
        raise HTTPException(400, "status must be unreviewed, approved or flagged")
    return store.set_review(run_id, draft_id, body.status, body.note)


@app.get("/api/runs/{run_id}/input/{name}")
def run_input(run_id: str, name: str) -> FileResponse:
    _run(run_id)
    path = store.dir(run_id) / "input" / _safe_name(name)
    if not path.is_file():
        raise HTTPException(404, "No such input file")
    return FileResponse(path)


def _submission(run_id: str) -> tuple[dict | None, dict]:
    """The submission with the current team id, and its export status."""
    run = _run(run_id)
    status = {"available": False, "case_id": run["case_id"], "code": None, "errors": [],
              "team_id_placeholder": settings.team_id in {"team-07", "team-xx"}}
    if not run["finished"]:
        return None, {**status, "code": "running", "reason": f"the run is still {run['state']}"}
    results = store.results(run_id)
    if not results:
        return None, {**status, "code": "no_submission", "reason": "this run produced no requirements"}
    if not settings.team_id:
        return None, {**status, "code": "team_missing", "reason": "CERTIVITY_TEAM_ID is not set"}
    sub = {"team_id": settings.team_id, "case_id": run["case_id"],
           "requirements": [{k: r[k] for k in ("id", "text", "source")} for r in results["requirements"]]}
    validation = checks.validate(sub)
    if not validation["valid"]:
        return None, {**status, "code": "invalid", "errors": validation["errors"]}
    return sub, {**status, "available": True, "code": "valid"}


@app.get("/api/runs/{run_id}/export")
def export_status(run_id: str) -> dict:
    return _submission(run_id)[1]


@app.get("/api/runs/{run_id}/submission")
def download_submission(run_id: str) -> Response:
    sub, status = _submission(run_id)
    if sub is None:
        return JSONResponse(status_code=409, content={"detail": status.get("reason") or status["code"]})
    filename = f"{sub['case_id']}_{sub['team_id']}_{run_id}.json"
    return Response(json.dumps(sub, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/runs/{run_id}/results")
def run_results(run_id: str) -> dict:
    run = _run(run_id)
    results = store.results(run_id) or {}
    reqs = results.get("requirements", [])
    docs = corpus.documents()
    documents, passages, view_reqs = {}, {}, []
    for r in reqs:
        s = r["source"]
        doc = docs.get(s["regulation"])
        document_id = doc.document_id if doc else f"{s['regulation'].replace(' ', '_')}@{s['version']}"
        documents[document_id] = {"document_id": document_id, "regulation": s["regulation"], "role": "base",
                                  "version_label": s["version"], "title": doc.title if doc else None}
        passage_id = f"{document_id}|{s['paragraph']}|{s['page']}"
        passages[passage_id] = {"passage_id": passage_id, "document_id": document_id, "text": r["paragraph_text"],
                                "paragraph_label": s["paragraph"], "heading_ancestry": r["heading"].split(" > ") if r["heading"] else [],
                                "printed_start_page": s["page"], "pdf_page_indices": [s["page"] - 1]}
        view_reqs.append({
            "draft_id": r["id"], "requirement_id": r["id"], "status": "accepted", "text": r["text"], "source": s,
            "primary_passage_id": passage_id, "supporting_passage_ids": [passage_id], "unresolved_references": [],
            "findings": [], "source_link": {"document_id": document_id, "pdf_page_index": s["page"] - 1, "printed_page": s["page"]},
        })

    applicable = {a["regulation"]: a for a in results.get("applicable", [])}
    decisions = [{"regulation": d.regulation, "version_label": d.version,
                  "status": "applicable" if d.regulation in applicable else "excluded",
                  "explanation": applicable[d.regulation]["reason"] if d.regulation in applicable else "Not selected for this product",
                  "conditions": {"focus": applicable[d.regulation].get("focus"),
                                 "skipped sections": applicable[d.regulation].get("skipped_sections")}
                                if d.regulation in applicable else None}
                 for d in docs.values()] if results else []
    per_reg = {reg: sum(r["source"]["regulation"] == reg for r in reqs) for reg in applicable}
    coverage_items = [{"document_id": docs[reg].document_id if reg in docs else reg, "section_id": "all obligation paragraphs",
                       "status": "processed", "explanation": f"{n} requirements kept"} for reg, n in per_reg.items()]
    coverage_items += [{"status": "failed", "explanation": w} for w in results.get("warnings", [])]
    counts: dict[str, int] = {}
    for item in coverage_items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    return {
        "run": run, "product_profile": results.get("product"), "applicability_decisions": decisions,
        "requirements": view_reqs, "passages": passages, "documents": documents,
        "coverage": {"items": coverage_items, "counts": counts}, "findings": [], "remaining_issues": [],
        # Every citation points at a paragraph the parser read on that page, so it is checked against the source.
        "review_complete": bool(results), "export_excluded": [], "export_blocking_issues": [], "unresolved_references": [],
        "pipeline_warnings": results.get("warnings", []), "view_warnings": [], "usage": results.get("usage"),
        "validation": results.get("validation"), "evaluation": results.get("evaluation"), "platform_metrics": None,
        "engineer_reviews": store.reviews(run_id),
        "submission": {"available": bool(reqs), "requirement_count": len(reqs)},
    }


@app.get("/api/compare")
def compare(a: str, b: str) -> dict:
    sides, cited = [], []
    for run_id in (a, b):
        run = _run(run_id)
        results = store.results(run_id) or {}
        sources = [r["source"] for r in results.get("requirements", [])]
        cited.append({
            "regulations": {s["regulation"] for s in sources},
            "pages": {f"{s['regulation']} {s['version']} p.{s['page']}" for s in sources},
            "paragraphs": {f"{s['regulation']} {s['paragraph']}" for s in sources},
        })
        sides.append({"run": {k: run.get(k) for k in ("run_id", "state", "origin", "case_id", "team_id", "created_at", "submission_valid")},
                      "requirements": len(sources), "evaluation": results.get("evaluation")})
    diff = {k: {"both": len(cited[0][k] & cited[1][k]), "only_a": sorted(cited[0][k] - cited[1][k]),
                "only_b": sorted(cited[1][k] - cited[0][k])} for k in ("regulations", "pages", "paragraphs")}
    return {"a": sides[0], "b": sides[1], **diff, "note": "Compares what is cited, not what the requirements say."}


if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
