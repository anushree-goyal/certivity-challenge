"""Batched model review of drafts against their actual evidence.

Model output is treated as a claim, not proof: verdicts are cross-checked (all six checks must
agree, and a 'supported' verdict needs a quote that really occurs in the evidence). Anything the
model did not (validly) review is reported as unreviewed - never silently accepted as reviewed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import _access as A
from ._records import make_finding
from .prompts.review_prompt import REVIEW_INSTRUCTIONS, REVIEW_SCHEMA

CHECKS = ("subject", "scope", "value", "unit", "condition", "exception")
OK_STATES = {"ok", "not_applicable"}
TABLE_FLAGS = {"table", "table_layout", "layout_uncertain", "image_only"}  # ASSUMED flag names

SEM_SUPPORTED = "semantic_supported"
SEM_UNSUPPORTED = "semantic_unsupported"
SEM_REVISION = "semantic_needs_revision"
SEM_CONTEXT = "semantic_needs_context"
SEM_UNREVIEWED = "semantic_unreviewed"


@dataclass
class SemanticConfig:
    batch_size: int = 5
    max_batch_chars: int = 24_000
    max_calls: int = 40
    use_vision: bool = True


@dataclass
class SemanticOutcome:
    supported: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    needs_revision: dict[str, str] = field(default_factory=dict)
    unreviewed: dict[str, str] = field(default_factory=dict)   # draft_id -> why
    findings: list = field(default_factory=list)
    calls: int = 0
    warnings: list[str] = field(default_factory=list)


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().casefold()


def _evidence_for(draft, passages) -> list:
    ids = list(dict.fromkeys(([A.draft_primary_id(draft)] if A.draft_primary_id(draft) else [])
                             + A.draft_supporting_ids(draft)))
    return [passages[i] for i in ids if i in passages]


def _payload_item(draft, evidence) -> dict:
    return {
        "draft_id": A.draft_id(draft),
        "text": A.draft_text(draft),
        "applicability_condition": A.draft_condition(draft),
        "primary_passage_id": A.draft_primary_id(draft),
        "evidence": [{"passage_id": A.passage_id(p), "paragraph": A.passage_paragraph(p),
                      "printed_page": A.passage_printed_page(p),
                      "headings": A.passage_heading(p), "text": A.passage_text(p)}
                     for p in evidence],
    }


def _batches(items: list[dict], cfg: SemanticConfig) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for it in items:
        n = sum(len(e["text"]) for e in it["evidence"]) + len(it["text"])
        if cur and (len(cur) >= cfg.batch_size or size + n > cfg.max_batch_chars):
            out.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += n
    if cur:
        out.append(cur)
    return out


def _page_images(batch_drafts, passages, corpus, warnings) -> list:
    """Page images for table/layout-flagged primary evidence (vision adapter input)."""
    images, seen = [], set()
    for d in batch_drafts:
        p = passages.get(A.draft_primary_id(d))
        if p is None or not (set(map(str, A.passage_flags(p))) & TABLE_FLAGS):
            continue
        for idx in A.passage_pdf_page_indices(p):
            key = (A.passage_document_id(p), idx)
            if key in seen:
                continue
            seen.add(key)
            try:
                images.append(corpus.get_page_asset(A.passage_document_id(p), idx))
            except Exception as e:  # noqa: BLE001
                warnings.append(f"page image unavailable for {key}: {e}")
    return images


def _interpret(review: dict, evidence_text: str):
    """Return (bucket, explanation, requested_context) or None if the review is unusable."""
    verdict = review.get("verdict")
    checks = review.get("checks")
    expl = (review.get("explanation") or "").strip()
    req = review.get("requested_context")
    if verdict not in {"supported", "unsupported", "needs_revision", "needs_context"}:
        return None
    if not isinstance(checks, dict) or any(checks.get(c) not in {*OK_STATES, "problem", "unclear"}
                                           for c in CHECKS):
        return None
    problems = {c for c in CHECKS if checks[c] == "problem"}
    unclear = {c for c in CHECKS if checks[c] == "unclear"}

    if verdict == "supported":
        quote = _squash(review.get("evidence_quote") or "")
        if problems or unclear:
            return ("revision", f"Model said supported but checks disagree "
                    f"(problem={sorted(problems)}, unclear={sorted(unclear)}). {expl}", req)
        if len(quote) < 8 or quote not in _squash(evidence_text):
            return ("revision", "Model said supported but its evidence quote is missing or does "
                    "not occur in the cited evidence, so the approval cannot be verified. " + expl,
                    req or "verify against the cited passage")
        return ("supported", expl, None)
    if verdict == "unsupported":
        if not expl:
            return None
        if problems & {"subject", "scope", "value", "unit"}:
            return ("rejected", expl, None)
        return ("revision", "Model found a problem limited to condition/exception, which may be "
                "recoverable. " + expl, req)
    if verdict == "needs_revision":
        return ("revision", expl or "Model requests revision.", req)
    return ("context", expl or "Meaning cannot be decided from the evidence given.", req)


def review_semantically(pipeline_draft, ids, corpus, llm, config: SemanticConfig | None = None):
    cfg = config or SemanticConfig()
    out = SemanticOutcome()
    drafts = {A.draft_id(d): d for d in A.drafts_of(pipeline_draft)}
    ids = [i for i in ids if i in drafts]
    if llm is None:
        out.unreviewed = {i: "no model adapter available" for i in ids}
        return out

    wanted = set()
    for i in ids:
        wanted |= set(A.draft_supporting_ids(drafts[i]))
        if A.draft_primary_id(drafts[i]):
            wanted.add(A.draft_primary_id(drafts[i]))
    passages = {A.passage_id(p): p for p in corpus.get_passages(sorted(wanted))}
    items = [_payload_item(drafts[i], _evidence_for(drafts[i], passages)) for i in ids]

    fatal = None
    for batch in _batches(items, cfg):
        batch_ids = [b["draft_id"] for b in batch]
        if out.calls >= cfg.max_calls:
            for i in batch_ids:
                out.unreviewed[i] = "model-call budget exhausted"
            continue
        images = (_page_images([drafts[i] for i in batch_ids], passages, corpus, out.warnings)
                  if cfg.use_vision else [])
        out.calls += 1
        try:
            resp = llm.generate_json(task="quality_review", instructions=REVIEW_INSTRUCTIONS,
                                     payload={"drafts": batch}, schema=REVIEW_SCHEMA,
                                     images=images or None)
            data = A.llm_data(resp)
            reviews = data.get("reviews") if isinstance(data, dict) else None
            if not isinstance(reviews, list):
                raise ValueError("response has no 'reviews' list")
        except Exception as e:  # noqa: BLE001
            for i in batch_ids:
                out.unreviewed[i] = f"review call failed: {type(e).__name__}: {e}"
            if type(e).__name__ == "LLMConfigurationError":  # no credential: later calls fail too
                out.warnings.append(f"model adapter not configured; semantic review skipped: {e}")
                fatal = f"not reviewed: model adapter not configured ({e})"
                break
            continue

        by_id: dict[str, list] = {}
        for r in reviews:
            if isinstance(r, dict) and r.get("draft_id") in batch_ids:
                by_id.setdefault(r["draft_id"], []).append(r)
        for i in batch_ids:
            rs = by_id.get(i, [])
            if len(rs) != 1:
                out.unreviewed[i] = ("model returned no review" if not rs
                                     else "model returned several conflicting reviews")
                continue
            ev = _evidence_for(drafts[i], passages)
            res = _interpret(rs[0], " ".join(A.passage_text(p) for p in ev))
            if res is None:
                out.unreviewed[i] = "model review was malformed"
                continue
            bucket, expl, req = res
            ev_ids = [A.passage_id(p) for p in ev]
            cat = {"supported": SEM_SUPPORTED, "rejected": SEM_UNSUPPORTED,
                   "revision": SEM_REVISION, "context": SEM_CONTEXT}[bucket]
            if bucket == "context":
                out.findings.append(make_finding(
                    i, cat, expl, ev_ids,
                    requested_context=req or f"additional context needed to decide: {expl}"))
            elif bucket == "revision":
                out.findings.append(make_finding(
                    i, cat, expl, ev_ids, requested_context=req,
                    requested_correction=expl or "revise the draft to match the evidence"))
            else:
                out.findings.append(make_finding(i, cat, expl, ev_ids))
            if bucket == "supported":
                out.supported.append(i)
            elif bucket == "rejected":
                out.rejected[i] = f"{cat}: {expl}"
            else:
                out.needs_revision[i] = cat
    if fatal:
        for i in ids:
            if i not in out.unreviewed and i not in out.supported and i not in out.rejected \
                    and i not in out.needs_revision:
                out.unreviewed[i] = fatal
    for i, why in out.unreviewed.items():
        out.findings.append(make_finding(i, SEM_UNREVIEWED, why))
    return out
