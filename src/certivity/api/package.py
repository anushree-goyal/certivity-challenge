"""Participant-package views for the app: cases, regulation files and corpus freshness.

Everything here is re-read from disk on each call, so a `git pull` in the package (new case folders,
new index entries) or a fresh `tools/fetch_regulations.py` shows up without restarting the app.
Corpus contents come only from the CorpusReader's list_documents(); the SQLite store is never read here.
Nothing returned contains a filesystem path, and expected.json files are never opened or listed.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from certivity.api.settings import Settings

SPEC_SUFFIXES = (".md", ".markdown", ".txt")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
HELD_OUT_PREFIX = "heldout-"


def _visible_files(folder: Path, suffixes: tuple[str, ...]) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in suffixes)


# --- cases --------------------------------------------------------------------------------------


def _case_sort_key(case_id: str) -> tuple[int, str]:
    return (0 if case_id.startswith(HELD_OUT_PREFIX) else 1, case_id)


def case_inputs(settings: Settings, case_id: str) -> dict[str, Any]:
    """Input files of a main-goal case, detected by extension. Raises KeyError for an unknown case."""
    case_dir = settings.cases_dir / case_id
    if not case_id or "/" in case_id or "\\" in case_id or case_id.startswith(".") or not case_dir.is_dir():
        raise KeyError(case_id)
    specs = _visible_files(case_dir, SPEC_SUFFIXES)
    # Prefer spec.md when several text files are present; otherwise the first one.
    chosen = next((p for p in specs if p.name.lower() == "spec.md"), specs[0] if specs else None)
    return {"case_id": case_id, "spec_path": chosen, "spec_files": specs, "drawing_paths": _visible_files(case_dir, IMAGE_SUFFIXES)}


def list_cases(settings: Settings) -> list[dict[str, Any]]:
    """Main-goal cases (cases/*) and sub-goal folders (subgoal/*), held-out cases first."""
    out: list[dict[str, Any]] = []
    if settings.cases_dir.is_dir():
        for folder in sorted(settings.cases_dir.iterdir(), key=lambda p: _case_sort_key(p.name)):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            case = case_inputs(settings, folder.name)
            out.append({
                "case_id": folder.name, "type": "main", "held_out": folder.name.startswith(HELD_OUT_PREFIX),
                "spec_file": case["spec_path"].name if case["spec_path"] else None,
                "spec_files": [p.name for p in case["spec_files"]],
                "drawings": [p.name for p in case["drawing_paths"]],
                "has_spec": case["spec_path"] is not None,
            })
    subgoal_dir = settings.participant_package / "subgoal"
    if subgoal_dir.is_dir():
        for folder in sorted(subgoal_dir.iterdir(), key=lambda p: _case_sort_key(p.name)):
            if folder.is_dir() and not folder.name.startswith("."):
                out.append({"case_id": folder.name, "type": "subgoal", "held_out": folder.name.startswith(HELD_OUT_PREFIX),
                            "spec_file": None, "spec_files": [], "drawings": [], "has_spec": False})
    return out


# --- regulation files ---------------------------------------------------------------------------

_hash_lock = threading.Lock()
_hash_cache: dict[tuple[str, int, int], str] = {}


def file_sha256(path: Path) -> str:
    """sha256 of a file, cached by (path, size, mtime) so repeated health checks do not re-hash 100+ MB."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _hash_lock:
        if key in _hash_cache:
            return _hash_cache[key]
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with _hash_lock:
        _hash_cache[key] = value
    return value


def regulation_slug(regulation: str) -> str:
    """Folder name tools/fetch_regulations.py uses under regulations/pdf/."""
    return "_".join(regulation.replace("/", " ").split())


def load_index(settings: Settings) -> list[dict[str, Any]]:
    if not settings.index_path.is_file():
        return []
    data = json.loads(settings.index_path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _version(entry: dict[str, Any], role: str) -> dict[str, Any] | None:
    return next((v for v in entry.get("versions", []) if isinstance(v, dict) and v.get("role") == role), None)


def _source_link(url: Any) -> str | None:
    """Only the original UNECE page is linked; the event's PDF download links expire and are never shown."""
    if isinstance(url, str) and (urlparse(url).hostname or "").endswith("unece.org"):
        return url
    return None


def base_file_status(settings: Settings, entry: dict[str, Any]) -> list[dict[str, Any]]:
    version = _version(entry, "base") or {}
    folder = settings.participant_package / "regulations" / "pdf" / regulation_slug(entry.get("regulation", "")) / "base"
    files = []
    for f in version.get("files", []) or []:
        local = folder / str(f.get("name", ""))
        present = local.is_file()
        verified = present and file_sha256(local) == f.get("sha256")
        files.append({"name": f.get("name"), "sha256": f.get("sha256"), "present": present, "verified": verified})
    return files


def documents_by_checksum(documents: list[Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for d in documents:
        get = (lambda k: d.get(k)) if isinstance(d, dict) else (lambda k: getattr(d, k, None))
        role = get("role")
        role = getattr(role, "value", role)
        if role in (None, "base") and get("checksum"):
            out[str(get("checksum"))] = {"document_id": get("document_id"), "page_count": get("page_count"),
                                         "regulation": get("regulation"), "snapshot_id": get("snapshot_id")}
    return out


def regulations_report(settings: Settings, documents: list[Any], corpus_ready: bool) -> dict[str, Any]:
    by_checksum = documents_by_checksum(documents)
    rows = []
    for entry in load_index(settings):
        base, latest = _version(entry, "base") or {}, _version(entry, "latest") or {}
        files = base_file_status(settings, entry)
        docs = [by_checksum[f["sha256"]] for f in files if f["sha256"] in by_checksum]
        downloaded = bool(files) and all(f["present"] for f in files)
        verified = bool(files) and all(f["verified"] for f in files)
        rows.append({
            "regulation": entry.get("regulation"),
            "title": entry.get("title"),
            "base_version": base.get("version_label"),
            "latest_version": latest.get("version_label"),
            "pdf": "verified" if verified else "checksum mismatch" if downloaded else "missing",
            "downloaded": downloaded,
            "verified": verified,
            "in_corpus": bool(files) and len(docs) == len(files),
            "page_count": sum(d["page_count"] or 0 for d in docs) or None,
            "document_id": docs[0]["document_id"] if docs else None,
            "files": [{"name": f["name"], "present": f["present"], "verified": f["verified"]} for f in files],
            "source_url": _source_link(entry.get("source_url")),
        })
    return {
        "summary": {"in_index": len(rows), "downloaded": sum(r["verified"] for r in rows),
                    "imported": sum(r["in_corpus"] for r in rows), "corpus_ready": corpus_ready},
        "regulations": rows,
    }


def corpus_freshness(settings: Settings, documents: list[Any], corpus_ready: bool) -> dict[str, Any]:
    """Compare verified downloaded base PDFs (index file list + checksums) with the active snapshot."""
    index = load_index(settings)
    wanted: dict[str, str] = {}  # sha256 -> regulation, for verified local base files
    missing, mismatched = [], []
    for entry in index:
        for f in base_file_status(settings, entry):
            label = f"{entry.get('regulation')} ({f['name']})"
            if f["verified"]:
                wanted[f["sha256"]] = entry.get("regulation")
            elif f["present"]:
                mismatched.append(label)
            else:
                missing.append(label)
    in_snapshot = documents_by_checksum(documents)
    not_imported = sorted({wanted[s] for s in wanted if s not in in_snapshot})
    no_longer_listed = sorted({str(d["regulation"]) for s, d in in_snapshot.items() if s not in wanted})
    reasons = []
    if not corpus_ready and wanted:
        reasons.append("No corpus snapshot has been imported yet.")
    if corpus_ready and not_imported:
        reasons.append(f"{len(not_imported)} downloaded regulation PDF(s) are not in the corpus snapshot.")
    if corpus_ready and no_longer_listed:
        reasons.append(f"{len(no_longer_listed)} regulation(s) in the snapshot no longer match the package index.")
    return {
        "needs_import": bool(reasons), "reasons": reasons,
        "not_imported": not_imported, "no_longer_listed": no_longer_listed,
        "missing_pdfs": missing, "checksum_mismatch": mismatched,
        "index_entries": len(index), "verified_pdfs": len(wanted),
    }
