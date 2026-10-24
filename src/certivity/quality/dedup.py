"""Genuine-duplicate handling.

Safe rule first: identical normalised text + same primary passage + same condition is a duplicate.
Near-duplicates are only candidates; a model confirms, and deterministic guards still require
equal numbers and equal conditions before anything is removed. Same wording from a DIFFERENT
primary passage is an independently imposed obligation and is kept.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from certivity.contracts import ModelUsage

from . import _access as A
from ._records import make_finding, usage_of
from .prompts.review_prompt import DEDUP_INSTRUCTIONS, DEDUP_SCHEMA, VERSION

DUPLICATE_REMOVED = "duplicate_removed"
POSSIBLE_DUPLICATE_UNRESOLVED = "possible_duplicate_unresolved"
DUPLICATE_KEPT_DISTINCT = "duplicate_candidate_kept_distinct"
NEAR_THRESHOLD = 0.8
MAX_GROUP = 6
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def norm_text(s: str) -> str:
    return re.sub(r"[\s.;:,]+$", "", re.sub(r"\s+", " ", s or "").strip().casefold())


def _nums(s: str) -> frozenset:
    return frozenset(m.replace(",", ".") for m in _NUM.findall(s or ""))


def _tokens(s: str) -> set:
    return set(re.findall(r"\w+", (s or "").casefold()))


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


@dataclass
class DedupOutcome:
    mapping: dict[str, str] = field(default_factory=dict)   # removed id -> kept id
    findings: list = field(default_factory=list)
    unresolved: list[list[str]] = field(default_factory=list)
    calls: int = 0
    usage: ModelUsage = field(default_factory=ModelUsage)


def exact_duplicates(drafts_by_id: dict, ids: list[str]) -> DedupOutcome:
    out, seen = DedupOutcome(), {}
    for i in ids:
        d = drafts_by_id[i]
        key = (norm_text(A.draft_text(d)), A.draft_primary_id(d), norm_text(A.draft_condition(d) or ""))
        if key in seen:
            out.mapping[i] = seen[key]
            out.findings.append(make_finding(
                i, DUPLICATE_REMOVED,
                f"Identical wording, primary passage and condition as {seen[key]!r}.",
                [A.draft_primary_id(d)] if A.draft_primary_id(d) else []))
        else:
            seen[key] = i
    return out


def near_duplicate_groups(drafts_by_id: dict, ids: list[str], corpus_doc_of) -> list[list[str]]:
    """Candidate groups: same document, identical number sets, high token overlap.

    Drafts are bucketed by (document, number set) first so only plausible pairs are compared.
    """
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    toks = {i: _tokens(A.draft_text(drafts_by_id[i])) for i in ids}
    norm = {i: norm_text(A.draft_text(drafts_by_id[i])) for i in ids}
    buckets: dict[tuple, list[str]] = {}
    for i in ids:
        d = drafts_by_id[i]
        buckets.setdefault((corpus_doc_of(d), _nums(A.draft_text(d))), []).append(i)
    for members in buckets.values():
        for a_idx, a in enumerate(members):
            for b in members[a_idx + 1:]:
                if norm[a] == norm[b] and A.draft_primary_id(drafts_by_id[a]) != \
                        A.draft_primary_id(drafts_by_id[b]):
                    continue  # independently imposed (different primary passage): keep both
                if _jaccard(toks[a], toks[b]) >= NEAR_THRESHOLD:
                    parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if 2 <= len(g) <= MAX_GROUP]


def near_duplicates(drafts_by_id, ids, passages, llm) -> DedupOutcome:
    out = DedupOutcome()
    doc_of = lambda d: A.passage_document_id(passages[A.draft_primary_id(d)]) \
        if A.draft_primary_id(d) in passages else None  # noqa: E731
    groups = near_duplicate_groups(drafts_by_id, ids, doc_of)
    if not groups:
        return out
    if llm is None:
        out.unresolved = groups
        for g in groups:
            out.findings.append(make_finding(
                g[0], POSSIBLE_DUPLICATE_UNRESOLVED,
                f"Drafts {g} look like near-duplicates but no model was available to confirm; "
                "all kept."))
        return out

    payload = {"groups": [{"group_id": f"g{n}", "drafts": [
        {"draft_id": i, "text": A.draft_text(drafts_by_id[i]),
         "applicability_condition": A.draft_condition(drafts_by_id[i]),
         "primary_passage_id": A.draft_primary_id(drafts_by_id[i]),
         "evidence_text": A.passage_text(passages[A.draft_primary_id(drafts_by_id[i])])
         if A.draft_primary_id(drafts_by_id[i]) in passages else ""} for i in g]}
        for n, g in enumerate(groups)]}
    out.calls = 1
    try:
        resp = llm.generate_json(task="quality_dedup", instructions=DEDUP_INSTRUCTIONS,
                                 payload=payload, schema=DEDUP_SCHEMA, images=None,
                                 prompt_version=VERSION)
        out.usage = usage_of(resp)
        data = A.llm_data(resp)
        verdicts = {v.get("group_id"): v for v in data["groups"] if isinstance(v, dict)}
    except Exception as e:  # noqa: BLE001
        out.unresolved = groups
        out.findings.append(make_finding(groups[0][0], POSSIBLE_DUPLICATE_UNRESOLVED,
                                         f"Duplicate confirmation failed ({type(e).__name__}); all kept."))
        return out

    for n, g in enumerate(groups):
        v = verdicts.get(f"g{n}")
        if not v or v.get("keep") not in g:
            out.unresolved.append(g)
            out.findings.append(make_finding(g[0], POSSIBLE_DUPLICATE_UNRESOLVED,
                                             f"No usable duplicate verdict for {g}; all kept."))
            continue
        keep = v["keep"]
        for i in v.get("duplicates") or []:
            if i == keep or i not in g:
                continue
            same_cond = norm_text(A.draft_condition(drafts_by_id[i]) or "") == \
                norm_text(A.draft_condition(drafts_by_id[keep]) or "")
            same_nums = _nums(A.draft_text(drafts_by_id[i])) == _nums(A.draft_text(drafts_by_id[keep]))
            if same_cond and same_nums:
                out.mapping[i] = keep
                out.findings.append(make_finding(i, DUPLICATE_REMOVED,
                                                 f"Duplicate of {keep!r}: {v.get('explanation', '')}"))
            else:
                out.findings.append(make_finding(
                    i, DUPLICATE_KEPT_DISTINCT,
                    f"Model called this a duplicate of {keep!r} but conditions or numbers differ; kept."))
    return out
