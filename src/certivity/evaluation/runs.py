"""Candidate/run identity so experiments can be compared and the chosen final file recovered."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .scoring import _pairs, _sort_key


def candidate_id(submission: dict) -> str:
    canon = json.dumps(submission, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def save_candidate(directory, submission: dict, meta: dict | None = None) -> str:
    """Write <id>.submission.json (exact bytes of what was validated) and <id>.meta.json.

    meta should carry run_id, corpus_snapshot_id, prompt versions, model identity, validation
    result, local-eval summary. Re-saving the same submission updates only the metadata.
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    cid = candidate_id(submission)
    sub_path = d / f"{cid}.submission.json"
    if not sub_path.exists():
        sub_path.write_text(json.dumps(submission, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / f"{cid}.meta.json").write_text(json.dumps(
        {"candidate_id": cid, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **(meta or {})},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return cid


def list_candidates(directory) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(Path(directory).glob("*.meta.json"))]


def load_candidate(directory, cid: str) -> dict:
    return json.loads((Path(directory) / f"{cid}.submission.json").read_text(encoding="utf-8"))


def compare_candidates(a: dict, b: dict) -> dict:
    pa, pb = set(_pairs(a)), set(_pairs(b))
    return {"a": candidate_id(a), "b": candidate_id(b),
            "requirements": [len(a.get("requirements", [])), len(b.get("requirements", []))],
            "pairs_only_in_a": sorted(pa - pb, key=_sort_key),
            "pairs_only_in_b": sorted(pb - pa, key=_sort_key),
            "scope": "regulation/version/page sets only; says nothing about requirement meaning"}


def write_report(report, directory, name: str) -> Path:
    from certivity.contracts import to_jsonable
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{name}.json"
    out.write_text(json.dumps(to_jsonable(report),
                              ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return out
