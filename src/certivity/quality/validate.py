"""validate_submission: independent structural checks + the official validator.

`valid` is True only if our checks pass AND the official validator actually ran and
passed. Passing validation does not establish that the requirements are correct.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from ._records import make_validation_error as err
from ._records import make_validation_report
from .deterministic import verified_page
from .limits import (MAX_OUTPUT_BYTES, MAX_REQUIREMENTS, MAX_TEXT_CHARS,
                     REQUIREMENT_KEYS, SUBMISSION_KEYS)

OFFICIAL_VALIDATOR_REL = Path("tools") / "validate.py"
SCHEMA_REL = Path("schemas") / "submission_main.schema.json"
INDEX_REL = Path("regulations") / "index.json"
OFFICIAL_TIMEOUT_S = 120


def structural_errors(submission) -> list:
    errors = []
    if not isinstance(submission, dict):
        return [err("$", "submission must be an object")]
    for k in SUBMISSION_KEYS:
        if k not in submission:
            errors.append(err(f"$.{k}", "missing required field"))
    for k in ("team_id", "case_id"):
        if k in submission and not (isinstance(submission[k], str) and submission[k].strip()):
            errors.append(err(f"$.{k}", "must be a non-empty string"))
    reqs = submission.get("requirements")
    if not isinstance(reqs, list):
        if "requirements" in submission:
            errors.append(err("$.requirements", "must be a list"))
        return errors
    if len(reqs) > MAX_REQUIREMENTS:
        errors.append(err("$.requirements", f"{len(reqs)} items exceeds {MAX_REQUIREMENTS}"))
    seen = set()
    for i, r in enumerate(reqs):
        base = f"$.requirements[{i}]"
        if not isinstance(r, dict):
            errors.append(err(base, "must be an object"))
            continue
        for k in REQUIREMENT_KEYS:
            if k not in r:
                errors.append(err(f"{base}.{k}", "missing required field"))
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            errors.append(err(f"{base}.id", "must be a non-empty string"))
        elif rid in seen:
            errors.append(err(f"{base}.id", f"duplicate requirement id {rid!r}"))
        else:
            seen.add(rid)
        text = r.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append(err(f"{base}.text", "must be a non-empty string"))
        elif len(text) > MAX_TEXT_CHARS:
            errors.append(err(f"{base}.text", f"{len(text)} characters exceeds {MAX_TEXT_CHARS}"))
        src = r.get("source")
        if not isinstance(src, dict):
            if "source" in r:
                errors.append(err(f"{base}.source", "must be an object"))
            continue
        for k in ("regulation", "version"):
            if not (isinstance(src.get(k), str) and src[k].strip()):
                errors.append(err(f"{base}.source.{k}", "must be a non-empty string"))
        page = src.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or verified_page(page) is None:
            errors.append(err(f"{base}.source.page", "must be a positive integer printed page"))
        if "paragraph" in src and not isinstance(src["paragraph"], str):
            errors.append(err(f"{base}.source.paragraph", "must be a string when present (omit it, never null)"))
    try:
        size = len(json.dumps(submission, ensure_ascii=False).encode("utf-8"))
        if size > MAX_OUTPUT_BYTES:
            errors.append(err("$", f"output is {size} bytes, over {MAX_OUTPUT_BYTES}"))
    except (TypeError, ValueError) as e:
        errors.append(err("$", f"not JSON-serialisable: {e}"))
    return errors


def run_official_validator(submission: dict, package: Path):
    """Return (ran, passed, output). CLI `python tools/validate.py <file>`: exit 0 valid, 1 problems,
    2 unreadable (checked against package commit 2104a8e). Needs Python 3.12+ and jsonschema.
    Run from the package root so it finds regulations/index.json and checks names/versions."""
    script = package / OFFICIAL_VALIDATOR_REL
    if not script.is_file():
        return False, False, f"official validator not found at {script}"
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "submission.json"
        f.write_text(json.dumps(submission, ensure_ascii=False), encoding="utf-8")
        try:
            proc = subprocess.run([sys.executable, str(script), str(f)], cwd=package,
                                  capture_output=True, text=True, timeout=OFFICIAL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return True, False, f"official validator timed out after {OFFICIAL_TIMEOUT_S}s"
    out = (proc.stdout + proc.stderr).strip()
    return True, proc.returncode == 0, out


def _identity(path: Path):
    """"<path> sha256:<first 16 hex>" so a run records exactly which file it was checked against."""
    if not path.is_file():
        return None
    return f"{path} sha256:{hashlib.sha256(path.read_bytes()).hexdigest()[:16]}"


def validate_submission(submission, participant_package_path):
    package = Path(participant_package_path)
    errors = structural_errors(submission)
    ran, passed, output = (False, False, "skipped: submission is not an object")
    if isinstance(submission, dict):
        ran, passed, output = run_official_validator(submission, package)
    if not ran:
        errors.append(err("$", f"official validator did not run: {output}"))
    elif not passed:
        errors.append(err("$", f"official validator rejected the submission: {output}"))
    return make_validation_report(
        valid=not errors and ran and passed, errors=errors, official_ran=ran, official_output=output,
        schema_identity=_identity(package / SCHEMA_REL), index_identity=_identity(package / INDEX_REL),
        checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
