"""Sub-goal: which requirements derived from an old version of a regulation must change in the new version, and why.

Code does the comparison: both PDFs are split into numbered paragraphs, old and new paragraphs are paired (same
number, or near-identical text when renumbered), and only paragraphs whose text changed, disappeared or is new go
on. The model decides, per affected requirement, whether it is unchanged, modified (with its new text) or removed,
and writes requirements for obligations in new paragraphs. Reasons, pages and paragraph numbers come from the
code's diff, in the form the worked example uses ("Paragraph 5.1.4 of UN R175 was amended in version 1.0.0: 'a'
became 'b'"), so every stated change can be checked in the PDFs.
"""

from __future__ import annotations

import difflib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf

from certivity import corpus
from certivity.config import settings
from certivity.llm import LLM
from certivity.pipeline import system

Emit = Callable[..., None]

VERSION = re.compile(r"version\s+([\d.]+)\s*\|")
# Limits stated as "not more than" / "not less than" are obligations too.
LIMIT = re.compile(r"\b(not|no) (more|less|greater|lower|higher) than\b", re.I)
BATCH = 8

DECIDE_SYSTEM = """You update a requirement list after a regulation was amended. For each case you get a paragraph in the
old version, the requirements derived from it, and the paragraphs of the new version where its content may now be
(the same number, or renumbered or split paragraphs with similar text). For every requirement decide:

- "unchanged" when the amendment does not change what that requirement obliges (wording that does not affect it,
  a change in another sentence of the paragraph, renumbering, editorial fixes);
- "modified" when its obligation changed (scope, condition, value, wording that changes the meaning); new_text is
  the old requirement copied word for word with only the amended words changed: keep its sentence order, subject
  and phrasing;
- "removed" only when none of the new paragraphs contains the obligation any more. A paragraph that was renumbered
  or split still contains its obligations.

In new_paragraph give the number of the new paragraph that now holds the obligation (empty when removed).
Answer for every requirement id."""

ADD_SYSTEM = """You write the requirements that new paragraphs of an amended regulation add. For each new paragraph, write one
requirement per distinct obligation it states (a "shall", a limit, a condition the product must meet), in the
style of the example requirements derived from the old version: the regulation's own subject and wording, made
standalone. Return no requirements for a paragraph that only defines, describes, or concerns administrative
procedure."""

DECIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["unchanged", "modified", "removed"]},
                    "new_text": {"type": "string"},
                    "new_paragraph": {"type": "string"},
                },
                "required": ["requirement_id", "status", "new_text", "new_paragraph"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}

ADD_SCHEMA = {
    "type": "object",
    "properties": {
        "paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "requirements": {"type": "array", "items": {"type": "string"}}},
                "required": ["id", "requirements"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["paragraphs"],
    "additionalProperties": False,
}


@dataclass
class Case:
    """Old paragraph, its counterpart in the new version (None when deleted), and the requirements from it."""
    old: corpus.Paragraph
    new: corpus.Paragraph | None
    requirements: list[dict] = field(default_factory=list)
    candidates: list[corpus.Paragraph] = field(default_factory=list)  # where the content may be in the new version


def _noop(**_: object) -> None:
    pass


def version_of(pdf: Path) -> str:
    with pymupdf.open(pdf) as doc:
        m = VERSION.search(doc[min(1, doc.page_count - 1)].get_text())
    return m.group(1) if m else "?"


def _norm(text: str) -> str:
    return " ".join(text.replace("’", "'").replace("“", '"').replace("”", '"').split())


def _similarity(a: corpus.Paragraph, b: corpus.Paragraph) -> float:
    return difflib.SequenceMatcher(None, _norm(a.text), _norm(b.text), autojunk=False).ratio()


def align(old: list[corpus.Paragraph], new: list[corpus.Paragraph]) -> dict[int, corpus.Paragraph | None]:
    """{id(old paragraph): new paragraph or None}. Same number first; a renumbered paragraph is found by its text."""
    by_label: dict[str, corpus.Paragraph] = {}
    for p in new:
        by_label.setdefault(p.label, p)
    pairs: dict[int, corpus.Paragraph | None] = {}
    taken: set[int] = set()
    for o in old:
        n = by_label.get(o.label)
        if n is not None and id(n) not in taken and _similarity(o, n) >= 0.5:
            pairs[id(o)] = n
            taken.add(id(n))
    free = [n for n in new if id(n) not in taken]
    for o in old:
        if id(o) in pairs:
            continue
        best, score = None, 0.0
        for n in free:
            if id(n) in taken or difflib.SequenceMatcher(None, o.text, n.text).real_quick_ratio() < 0.6:
                continue
            s = _similarity(o, n)
            if s > score:
                best, score = n, s
        if best is not None and score >= 0.6:
            pairs[id(o)] = best
            taken.add(id(best))
        else:
            pairs[id(o)] = None
    return pairs


def diff_phrases(old: str, new: str, limit: int = 3) -> list[str]:
    """Word-level changes as "'a' became 'b'", "'b' was added", "'a' was removed"."""
    a, b = _norm(old).split(), _norm(new).split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        before, after = " ".join(a[i1:i2])[:120], " ".join(b[j1:j2])[:120]
        if op == "replace":
            out.append(f"'{before}' became '{after}'")
        elif op == "insert":
            out.append(f"'{after}' was added")
        elif op == "delete":
            out.append(f"'{before}' was removed")
    return out[:limit]


def _coverage(needle: str, haystack: str) -> float:
    """Share of the needle's text found in the haystack (in order): how much of a requirement a paragraph contains."""
    a, b = _norm(needle), _norm(haystack)
    blocks = difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()
    return sum(m.size for m in blocks if m.size >= 4) / max(1, len(a))


def _label_of(r: dict) -> str:
    return str(r["source"].get("paragraph") or "").strip().rstrip(".")


def run(case_dir: Path, llm: LLM, emit: Emit = _noop) -> dict:
    old_reqs = json.loads((case_dir / "requirements_old.json").read_text())
    regulation = next((r["source"]["regulation"] for r in old_reqs if r.get("source", {}).get("regulation")), "")
    old_version, new_version = version_of(case_dir / "old.pdf"), version_of(case_dir / "new.pdf")
    old = corpus.parse_pdf(regulation, old_version, case_dir / "old.pdf")
    new = corpus.parse_pdf(regulation, new_version, case_dir / "new.pdf")
    emit(stage="compare", message=f"{regulation} {old_version} -> {new_version}: {len(old)} and {len(new)} paragraphs")

    pairs = align(old, new)
    old_by_label: dict[str, corpus.Paragraph] = {}
    for p in old:
        old_by_label.setdefault(p.label, p)

    cases: dict[int, Case] = {}
    unplaced = []
    for r in old_reqs:
        o = old_by_label.get(_label_of(r))
        if o is None:
            # Cited by something other than a paragraph number (a table id): the old paragraph on the cited page,
            # or the next, that contains most of the requirement's text.
            page = r["source"].get("page")
            nearby = [p for p in old if page and abs(p.page - page) <= 1]
            o = max(nearby, key=lambda p: _coverage(r["text"], p.text), default=None)
        if o is None:
            unplaced.append(r["id"])
            continue
        n = pairs[id(o)]
        if n is not None and _norm(n.text) == _norm(o.text):
            continue  # paragraph unchanged, so the requirement is too
        cases.setdefault(id(o), Case(o, n)).requirements.append(r)
    for c in cases.values():
        # Per requirement, the new paragraphs that contain most of its text: a split or renumbered paragraph
        # keeps its obligations under other numbers.
        found = [c.new] if c.new is not None else []
        for r in c.requirements:
            # One way on purpose: offering every loosely similar paragraph made the model call real changes
            # "unchanged" (dev recall 1.00 -> 0.57); the sub-goal is scored on recall of the flagged changes.
            ranked = sorted(new, key=lambda n: _coverage(r["text"], n.text), reverse=True)
            found += [n for n in ranked[:3] if _coverage(r["text"], n.text) >= 0.3]
        c.candidates = list({id(n): n for n in found}.values())[:6]
    matched_new = {id(n) for n in pairs.values() if n is not None}
    matched_new |= {id(n) for c in cases.values() for n in c.candidates}
    added_paragraphs = [n for n in new if id(n) not in matched_new and (n.is_obligation or LIMIT.search(n.text))]
    emit(stage="compare", message=f"{sum(len(c.requirements) for c in cases.values())} requirements on changed "
                                  f"paragraphs, {len(added_paragraphs)} new paragraphs with obligations")

    examples = "\n".join(f"- {r['text']}" for r in old_reqs[:8])

    def decide(batch: list[Case]) -> list[dict]:
        text = "\n\n".join(
            f"CASE {i}\nOLD paragraph {c.old.label}:\n{c.old.text[:2500]}\n"
            "REQUIREMENTS:\n" + "\n".join(f"  {r['id']}: {r['text']}" for r in c.requirements) + "\n"
            + ("NEW candidate paragraphs:\n" + "\n".join(f"  [{n.label}] {n.text[:1500]}" for n in c.candidates)
               if c.candidates else "NEW version: no paragraph contains this content")
            for i, c in enumerate(batch)
        )
        answer = llm.json(model=settings.model_select, system=system(DECIDE_SYSTEM), schema=DECIDE_SCHEMA,
                          effort="medium", content=[{"type": "text", "text": f"Regulation {regulation}.\n\n{text}"}])
        return answer["decisions"]

    def add(batch: list[corpus.Paragraph]) -> list[tuple[corpus.Paragraph, str]]:
        text = "\n\n".join(f"[P{i}] paragraph {p.label} (section: {p.heading or '-'})\n{p.text[:2500]}"
                           for i, p in enumerate(batch))
        answer = llm.json(model=settings.model_select, system=system(ADD_SYSTEM), schema=ADD_SCHEMA, effort="medium",
                          content=[{"type": "text", "text": f"Regulation {regulation}. Example requirements from the "
                                                            f"old version:\n{examples}\n\nNew paragraphs:\n\n{text}"}])
        out = []
        for item in answer["paragraphs"]:
            index = item["id"].strip("[]P ")
            if index.isdigit() and int(index) < len(batch):
                out += [(batch[int(index)], t.strip()) for t in item["requirements"] if t.strip()]
        return out

    case_list = list(cases.values())
    with ThreadPoolExecutor(settings.parallel) as executor:
        decided = executor.map(decide, [case_list[i:i + BATCH] for i in range(0, len(case_list), BATCH)])
        written = executor.map(add, [added_paragraphs[i:i + BATCH] for i in range(0, len(added_paragraphs), BATCH)])
        decisions = {d["requirement_id"]: d for batch in decided for d in batch}
        additions = [x for batch in written for x in batch]

    changes = []
    for c in case_list:
        for r in c.requirements:
            d = decisions.get(r["id"])
            if not d or d["status"] == "unchanged":
                continue
            target = next((n for n in c.candidates if n.label == d["new_paragraph"].strip().rstrip(".")), c.new)
            if d["status"] == "modified" and target is not None and d["new_text"].strip():
                moved = f" (formerly {c.old.label})" if target.label != c.old.label else ""
                # The expert's reasons quote what changed in the obligation, so diff the requirement itself.
                what = "; ".join(diff_phrases(r["text"], d["new_text"])) or "the wording changed"
                changes.append({
                    "requirement_id": r["id"], "change": "modified", "new_text": d["new_text"].strip()[:2000],
                    "reason": f"Paragraph {target.label}{moved} of {regulation} was amended in version {new_version}: {what}.",
                    "source": {"page": target.page, "paragraph": target.label},
                })
            elif d["status"] == "removed":
                changes.append({"requirement_id": r["id"], "change": "removed",
                                "reason": f"Paragraph {c.old.label} of {regulation} was amended in version {new_version}; "
                                          f"the provision '{_norm(r['text'])[:80]}…' no longer appears."})
    for p, text in additions:
        changes.append({
            "requirement_id": None, "change": "added", "new_text": text[:2000],
            "reason": f"Paragraph {p.label} of {regulation} is new in version {new_version}: '{_norm(p.text)[:100]}…'.",
            "source": {"page": p.page, "paragraph": p.label},
        })
    return {"regulation": regulation, "old_version": old_version, "new_version": new_version, "changes": changes,
            "unplaced": unplaced, "requirements_old": len(old_reqs)}


def submission(team_id: str, case_id: str, result: dict) -> dict:
    return {"team_id": team_id, "case_id": case_id, "changes": result["changes"]}
