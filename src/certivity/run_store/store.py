"""File-based run storage.

Layout of one run:

    runs/<run_id>/
        manifest.json      state, stage, origin, timings, errors, cache key   (app-owned)
        events.jsonl       RunEvents emitted by every stage                   (app-owned)
        input/             immutable copies of the specification and drawings (app-owned)
        app/               results view, evidence, submission.json            (app-owned)
        agent/             extraction artifacts and model-call checkpoints    (Anushree)
        quality/           review artifacts                                   (Teammate 3)

Writes go through a temp file and rename so a crash never leaves a half-written manifest.
"""

from __future__ import annotations

import enum
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODULE_DIRS = ("agent", "quality", "app")


class RunState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"

    @property
    def finished(self) -> bool:
        return self in (RunState.COMPLETED, RunState.PARTIAL, RunState.FAILED)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class RunStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # --- paths -----------------------------------------------------------------------------

    def run_dir(self, run_id: str) -> Path:
        if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
            raise KeyError(run_id)
        return self.root / run_id

    def module_dir(self, run_id: str, module: str) -> Path:
        if module not in MODULE_DIRS:
            raise ValueError(f"unknown module folder {module!r}")
        return self.run_dir(run_id) / module

    def exists(self, run_id: str) -> bool:
        try:
            return (self.run_dir(run_id) / "manifest.json").is_file()
        except KeyError:
            return False

    # --- creation --------------------------------------------------------------------------

    def create(self, manifest: dict[str, Any], files: dict[str, bytes]) -> dict[str, Any]:
        """Create a run with immutable input files. `files` maps a file name under input/ to bytes."""
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
        run_dir = self.run_dir(run_id)
        (run_dir / "input").mkdir(parents=True)
        for module in MODULE_DIRS:
            (run_dir / module).mkdir()
        for name, content in files.items():
            target = run_dir / "input" / Path(name).name
            target.write_bytes(content)
            target.chmod(0o444)
        manifest = {
            "run_id": run_id,
            "state": RunState.QUEUED.value,
            "stage": "queued",
            "created_at": now_iso(),
            "started_at": None,
            "finished_at": None,
            "warnings": [],
            "errors": [],
            "interrupted": False,
            **manifest,
        }
        _write_json_atomic(run_dir / "manifest.json", manifest)
        (run_dir / "events.jsonl").touch()
        return manifest

    # --- manifest --------------------------------------------------------------------------

    def read(self, run_id: str) -> dict[str, Any]:
        path = self.run_dir(run_id) / "manifest.json"
        if not path.is_file():
            raise KeyError(run_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def update(self, run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            manifest = self.read(run_id)
            manifest.update(changes)
            manifest["updated_at"] = now_iso()
            _write_json_atomic(self.run_dir(run_id) / "manifest.json", manifest)
            return manifest

    def append_list(self, run_id: str, key: str, item: Any) -> None:
        with self._lock:
            manifest = self.read(run_id)
            manifest.setdefault(key, []).append(item)
            _write_json_atomic(self.run_dir(run_id) / "manifest.json", manifest)

    def list_runs(self) -> list[dict[str, Any]]:
        runs = []
        for path in self.root.glob("*/manifest.json"):
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(runs, key=lambda m: m.get("created_at", ""), reverse=True)

    # --- events ----------------------------------------------------------------------------

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock, open(self.run_dir(run_id) / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_events(self, run_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        path = self.run_dir(run_id) / "events.jsonl"
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        if limit is not None:
            lines = lines[-limit:]
        return [json.loads(line) for line in lines if line.strip()]

    # --- artifacts -------------------------------------------------------------------------

    def write_artifact(self, run_id: str, name: str, data: Any, module: str = "app") -> Path:
        path = self.module_dir(run_id, module) / name
        _write_json_atomic(path, data)
        return path

    def read_artifact(self, run_id: str, name: str, module: str = "app") -> Any:
        path = self.module_dir(run_id, module) / name
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def artifact_path(self, run_id: str, name: str, module: str = "app") -> Path:
        return self.module_dir(run_id, module) / name

    # --- lifecycle -------------------------------------------------------------------------

    def mark_interrupted(self) -> list[str]:
        """After a restart, no run can still be in progress: mark unfinished runs failed and say why."""
        interrupted = []
        for manifest in self.list_runs():
            if RunState(manifest["state"]).finished:
                continue
            run_id = manifest["run_id"]
            was = manifest["state"]
            self.update(
                run_id,
                state=RunState.FAILED.value,
                interrupted=True,
                finished_at=now_iso(),
                errors=[*manifest.get("errors", []),
                        {"stage": manifest.get("stage"), "message": f"Interrupted: the server restarted while the run was {was}."}],
            )
            interrupted.append(run_id)
        return interrupted

    def find_cached(self, cache_key: str) -> dict[str, Any] | None:
        """The newest fresh run with this cache key that produced a validated submission."""
        for manifest in self.list_runs():
            if (manifest.get("cache_key") == cache_key and manifest.get("origin") == "fresh"
                    and manifest.get("state") in (RunState.COMPLETED.value, RunState.PARTIAL.value)
                    and manifest.get("submission_valid")):
                return manifest
        return None
