"""evaluate_submission. The ONLY module allowed to read the golden answer (expected.json).

SCOPE: regulation/page level only. Requirement meaning is judged on the scoring platform, so the
semantic and platform scores stay None. Requirement count and text overlap are never reported
as accuracy.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .report import EvaluationReport

SCOPE = ("regulation/page-level coverage only; requirement-level meaning is NOT measured here "
         "(semantic/platform scores are null until recorded from the scoring platform)")
SCORER_REL = Path("tools") / "score_local.py"

# Miss categories from the brief. Several cannot be told apart from output files alone.
CAT_REG_NOT_COVERED = "regulation_not_covered"          # umbrella: see run_context to refine
CAT_SOURCE_UNAVAILABLE = "source_unavailable"
CAT_EXCLUDED = "applicable_regulation_excluded"
CAT_UNREAD = "unread_section"
CAT_WRONG_VERSION_PAGE = "wrong_version_or_page"
CAT_UNSUPPORTED = "unsupported_output"
# Not derivable from regulation/page data; reserved for manual or run-artifact analysis:
MANUAL_ONLY = ["lost_reference", "table_or_parse_failure", "merged_obligation", "incorrect_condition"]


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _pairs(doc) -> list[tuple[str, str, int | None]]:
    """(regulation, version, page) per requirement. Version is part of the key, as in the official
    scorer: a page of the latest version is not a page of the base version."""
    out = []
    for r in (doc.get("requirements") or []) if isinstance(doc, dict) else []:
        s = (r or {}).get("source") or {}
        reg, page = s.get("regulation"), s.get("page")
        if reg is not None:
            out.append((str(reg), str(s.get("version") or ""),
                        page if isinstance(page, int) and not isinstance(page, bool) else None))
    return out


def _sort_key(t):
    return (t[0], t[1], t[2] or 0)


def _prf(pred: set, gold: set) -> dict:
    tp = len(pred & gold)
    p = tp / len(pred) if pred else None
    r = tp / len(gold) if gold else None
    f1 = (2 * p * r / (p + r)) if p and r else (0.0 if pred and gold else None)
    return {"precision": p, "recall": r, "f1": f1, "true_positives": tp,
            "predicted": len(pred), "expected": len(gold)}


def classify_misses(sub_pairs: set, exp_pairs: set, run_context: dict | None = None) -> dict:
    """Best-effort categories. These are CANDIDATES from file comparison, not verified causes."""
    ctx = run_context or {}
    unavailable = set(ctx.get("unavailable_regulations", []))
    excluded = set(ctx.get("excluded_regulations", []))
    unread = set(ctx.get("unread_regulations", []))
    sub_regs = {r for r, _, _ in sub_pairs}
    exp_regs = {r for r, _, _ in exp_pairs}
    exp_versions = {(r, v) for r, v, _ in exp_pairs}
    cats: dict[str, list] = {}

    def add(cat, reg, ver, page, note):
        cats.setdefault(cat, []).append({"regulation": reg, "version": ver, "page": page, "note": note})

    for reg, ver, page in sorted(exp_pairs - sub_pairs, key=_sort_key):
        same_version = [(r, v, p) for r, v, p in sub_pairs if r == reg and v == ver]
        if reg in unavailable:
            add(CAT_SOURCE_UNAVAILABLE, reg, ver, page, "regulation reported unavailable in run context")
        elif reg in excluded:
            add(CAT_EXCLUDED, reg, ver, page, "regulation excluded by applicability in run context")
        elif reg in unread:
            add(CAT_UNREAD, reg, ver, page, "regulation/section not read according to run context")
        elif reg not in sub_regs:
            add(CAT_REG_NOT_COVERED, reg, ver, page,
                "no output cites this regulation; cause unknown without run artifacts")
        elif not same_version:
            add(CAT_WRONG_VERSION_PAGE, reg, ver, page,
                "regulation cited, but never in this version; possible wrong version")
        elif page is not None and any(p is not None and abs(p - page) <= 1 for _, _, p in same_version):
            add(CAT_WRONG_VERSION_PAGE, reg, ver, page, "output cites an adjacent page; possible wrong page")
        else:
            add("page_not_covered", reg, ver, page, "regulation cited, but not this page")
    for reg, ver, page in sorted(sub_pairs - exp_pairs, key=_sort_key):
        if reg not in exp_regs:
            add(CAT_UNSUPPORTED, reg, ver, page, "regulation absent from the expected answer (possible false positive)")
        elif (reg, ver) not in exp_versions:
            add(CAT_WRONG_VERSION_PAGE, reg, ver, page,
                "cites a version the expected answer does not use; possible wrong version")
    return cats


def run_official_scorer(package: Path, submission_path, expected_path) -> dict:
    """`python tools/score_local.py <submission> --expected <expected> --json` (checked against
    package commit 2104a8e). Raw output is kept; `parsed` holds regulation_level/page_level."""
    script = package / SCORER_REL
    if not script.is_file():
        return {"ran": False, "error": f"scorer not found at {script}"}
    try:
        proc = subprocess.run([sys.executable, str(script), str(submission_path), "--expected", str(expected_path), "--json"],
                              cwd=package, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"ran": True, "returncode": None, "error": "scorer timed out"}
    res = {"ran": True, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    try:
        res["parsed"] = json.loads(proc.stdout)
    except ValueError:
        res["parsed"] = None
    return res


def evaluate_submission(submission_path, expected_path, participant_package_path=None,
                        run_context: dict | None = None) -> EvaluationReport:
    sub, exp = _load(submission_path), _load(expected_path)
    sp, ep = _pairs(sub), _pairs(exp)
    s_pairs, e_pairs = set(sp), set(ep)
    s_regs, e_regs = {t[0] for t in s_pairs}, {t[0] for t in e_pairs}
    notes = ["Own computation on regulation and (regulation, version, page) sets. The official "
             "local scorer also credits a page when its paragraph matches, so quote its numbers "
             "(official_local_scorer.parsed) when it ran; the platform's page score differs again.",
             "Category assignments are candidates from file comparison, not confirmed causes. "
             f"Not derivable here: {', '.join(MANUAL_ONLY)}."]
    official = None
    if participant_package_path is not None:
        official = run_official_scorer(Path(participant_package_path), submission_path, expected_path)
    return EvaluationReport(
        scope=SCOPE,
        counts={"submitted_requirements": len(sp), "expected_requirements": len(ep),
                "submitted_pairs": len(s_pairs), "expected_pairs": len(e_pairs)},
        regulation_level=_prf(s_regs, e_regs),
        page_level=_prf(s_pairs, e_pairs),
        error_categories=classify_misses(s_pairs, e_pairs, run_context),
        official_local_scorer=official,
        notes=notes,
    )
