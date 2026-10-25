"""Runs on disk (runs/<id>/) and the single background worker that processes them.

runs/<id>/run.json      status, progress, events
runs/<id>/input/        the specification and drawings as uploaded
runs/<id>/results.json  what the pipeline produced
runs/<id>/reviews.json  engineer reviews (never change the submission)
"""

from __future__ import annotations

import hashlib
import json
import queue
import secrets
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from certivity import checks, pipeline
from certivity.config import settings
from certivity.llm import LLM, credentials_configured

FINISHED = {"completed", "partial", "failed"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunStore:
    def __init__(self, root: Path = settings.runs_dir) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.queue: queue.Queue[str] = queue.Queue()
        self.active: str | None = None

    # --- records ---------------------------------------------------------------------------

    def dir(self, run_id: str) -> Path:
        path = (self.root / run_id).resolve()
        if path.parent != self.root.resolve():
            raise KeyError(run_id)
        return path

    def get(self, run_id: str) -> dict:
        path = self.dir(run_id) / "run.json"
        if not path.is_file():
            raise KeyError(run_id)
        return json.loads(path.read_text())

    def save(self, run: dict) -> None:
        with self.lock:
            path = self.dir(run["run_id"]) / "run.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(run, indent=1))
            tmp.replace(path)

    def update(self, run_id: str, **fields) -> dict:
        with self.lock:
            run = self.get(run_id)
            run.update(fields)
            self.save(run)
            return run

    def event(self, run_id: str, *, stage: str, message: str, level: str = "info",
              completed: int | None = None, total: int | None = None) -> None:
        with self.lock:
            run = self.get(run_id)
            progress = {"stage": stage, "message": message, "completed": completed, "total": total}
            run["events"].append({"timestamp": now(), "level": level, **progress})
            run.update(stage=stage, progress=progress)
            self.save(run)

    def list(self) -> list[dict]:
        runs = []
        for path in self.root.glob("*/run.json"):
            try:
                runs.append(json.loads(path.read_text()))
            except ValueError:
                continue
        return sorted(runs, key=lambda r: r["created_at"], reverse=True)

    def results(self, run_id: str) -> dict | None:
        path = self.dir(run_id) / "results.json"
        return json.loads(path.read_text()) if path.is_file() else None

    def reviews(self, run_id: str) -> dict:
        path = self.dir(run_id) / "reviews.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def set_review(self, run_id: str, draft_id: str, status: str, note: str) -> dict:
        with self.lock:
            reviews = self.reviews(run_id)
            review = {"status": status, "note": note, "updated_at": now()}
            if status == "unreviewed" and not note:
                reviews.pop(draft_id, None)
            else:
                reviews[draft_id] = review
            (self.dir(run_id) / "reviews.json").write_text(json.dumps(reviews, indent=1))
            return review

    # --- lifecycle -------------------------------------------------------------------------

    def create(self, *, case_id: str, spec: tuple[str, bytes] | None, drawings: list[tuple[str, bytes]]) -> dict:
        run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
        input_dir = self.dir(run_id) / "input"
        input_dir.mkdir(parents=True)
        digest = hashlib.sha256()
        for name, data in ([spec] if spec else []) + drawings:
            (input_dir / name).write_bytes(data)
            digest.update(name.encode() + data)
        run = {
            "run_id": run_id, "state": "queued", "stage": "queued", "origin": "fresh", "mode": "real",
            "team_id": settings.team_id, "case_id": case_id, "created_at": now(), "started_at": None,
            "finished_at": None, "finished": False, "progress": {"stage": "queued", "message": "Waiting to start"},
            "warnings": [], "errors": [], "events": [], "cancel_requested": False, "cancelled": False,
            "interrupted": False, "submission_valid": None, "corpus_snapshot_id": None,
            "spec_file": spec[0] if spec else None, "drawing_files": [n for n, _ in drawings],
            "input_hash": digest.hexdigest()[:16],
        }
        self.save(run)
        self.queue.put(run_id)
        return run

    def cancel(self, run_id: str) -> dict:
        run = self.get(run_id)
        if run["state"] == "queued":
            return self.update(run_id, state="failed", stage="cancelled", finished=True, finished_at=now(),
                               cancelled=True, errors=[{"stage": "queued", "message": "Cancelled before it started"}])
        if run["state"] == "running":
            return self.update(run_id, cancel_requested=True)
        return run

    def mark_interrupted(self) -> None:
        """A restart loses whatever was running or queued; say so rather than leaving it spinning."""
        for run in self.list():
            if not run["finished"]:
                self.update(run["run_id"], state="failed", finished=True, finished_at=now(), interrupted=True,
                            errors=[{"stage": run["stage"], "message": "The server stopped before this run finished"}])

    def start_worker(self) -> None:
        threading.Thread(target=self._work, daemon=True, name="run-worker").start()

    def _work(self) -> None:
        while True:
            run_id = self.queue.get()
            if self.get(run_id)["state"] != "queued":
                continue
            self.active = run_id
            try:
                self.process(run_id)
            finally:
                self.active = None

    def process(self, run_id: str) -> None:
        run = self.update(run_id, state="running", started_at=now())
        input_dir = self.dir(run_id) / "input"
        spec_text = (input_dir / run["spec_file"]).read_text(errors="replace") if run["spec_file"] else ""
        drawings = [input_dir / n for n in run["drawing_files"]]
        llm = None
        try:
            if not credentials_configured():
                raise RuntimeError("No model credential: set ANTHROPIC_API_KEY in .env")
            llm = LLM()
            out = pipeline.run(
                spec_text, drawings, llm,
                emit=lambda **e: self.event(run_id, **e),
                cancelled=lambda: self.get(run_id)["cancel_requested"],
            )
            reqs = out["requirements"]
            sub = pipeline.submission(settings.team_id or "team-id-not-set", run["case_id"], reqs)
            validation = checks.validate(sub)
            self.event(run_id, stage="checks", message="Official validator: " + ("valid" if validation["valid"] else "invalid"))
            results = {
                "product": out["product"], "applicable": out["applicable"], "candidates": out["candidates"],
                "requirements": [{**r.submission(), "heading": r.paragraph.heading, "paragraph_text": r.paragraph.text}
                                 for r in reqs],
                "warnings": out["warnings"], "usage": llm.usage.as_dict(), "validation": validation,
                "evaluation": checks.score(sub, run["case_id"]),
            }
            (self.dir(run_id) / "results.json").write_text(json.dumps(results, indent=1))
            state = "completed" if validation["valid"] and not out["warnings"] else "partial"
            self.update(run_id, state=state, finished=True, finished_at=now(), warnings=out["warnings"],
                        submission_valid=validation["valid"])
            self.event(run_id, stage="done", message=f"{len(reqs)} requirements")
        except pipeline.Cancelled:
            self.update(run_id, state="failed", finished=True, finished_at=now(), cancelled=True,
                        errors=[{"stage": self.get(run_id)["stage"], "message": "Cancelled"}])
        except Exception as exc:
            traceback.print_exc()
            self.update(run_id, state="failed", finished=True, finished_at=now(),
                        errors=[{"stage": self.get(run_id)["stage"], "message": str(exc)}])
            self.event(run_id, stage="failed", message=str(exc), level="error")
