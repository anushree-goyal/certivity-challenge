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
Answer for every requirement id.

Also list in additions every obligation the new paragraph(s) of a case state that none of that case's requirements
covers: a sentence, list item or limit the amendment added. Only genuinely new obligations, not rewordings of an
existing requirement; write each one standalone, in the style of the old requirements, and give the case number and
the new paragraph that states it."""

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
        },
        "additions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"case": {"type": "integer"}, "new_paragraph": {"type": "string"},
                               "text": {"type": "string"}},
                "required": ["case", "new_paragraph", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decisions", "additions"],
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


SENTENCE = re.compile(r"(?<=[.;:])\s+(?=[A-Z(])|\s+(?=\([a-z]\)\s)")


def _sentences(text: str) -> list[str]:
    return [x.strip() for x in SENTENCE.split(_norm(text)) if x.strip()]


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower(), autojunk=False).ratio()


def _what_changed(old_text: str, new_text: str, requirement: str) -> str:
    """The regulation words behind an added obligation, as the worked example quotes them: the sentence of the new
    paragraph that states it, either new ("'...' was added") or reworded from the closest old sentence."""
    new_sentences = _sentences(new_text) or [_norm(new_text)]
    stated = max(new_sentences, key=lambda x: _ratio(x, _norm(requirement)))
    old_sentences = _sentences(old_text)
    closest = max(old_sentences, key=lambda x: _ratio(x, stated), default="")
    if not closest or _ratio(closest, stated) < 0.5:
        return f"'{stated[:200]}' was added"
    return "; ".join(diff_phrases(closest, stated)) or f"'{stated[:200]}' was added"


def _label_of(r: dict) -> str:
    return str(r["source"].get("paragraph") or "").strip().rstrip(".")


RECHECK_SYSTEM = """A regulation paragraph was amended. For each requirement derived from the old paragraph, compare it with
the new paragraph(s) and write the requirement as it reads under the new version: copy the old requirement word for
word and change only what the amendment changed for it, however small (a value, a condition, a reference, a term,
a scope word). If the amendment does not touch this requirement at all, return its old text unchanged."""

RECHECK_SCHEMA = {
    "type": "object",
    "properties": {"requirements": {"type": "array", "items": {
        "type": "object",
        "properties": {"requirement_id": {"type": "string"}, "new_text": {"type": "string"},
                       "new_paragraph": {"type": "string"}},
        "required": ["requirement_id", "new_text", "new_paragraph"], "additionalProperties": False}}},
    "required": ["requirements"],
    "additionalProperties": False,
}


def run(case_dir: Path, llm: LLM, emit: Emit = _noop, flag_all: bool | None = None) -> dict:
    """flag_all: also re-check the requirements judged unchanged on amended paragraphs and flag every one whose
    text changes at all. The sub-goal is scored on recall and the accuracy of each flag, so this trades the
    displayed precision for recall."""
    flag_all = settings.subgoal_flag_all if flag_all is None else flag_all
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
    placed: list[corpus.Paragraph] = []
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
            placed.append(o)
            continue  # paragraph unchanged, so the requirement is too
        cases.setdefault(id(o), Case(o, n)).requirements.append(r)
        placed.append(o)
    # The parts of the regulation the old requirement list tracks (sections and annexes it has requirements in):
    # new requirements are only expected there. On the held-out case most annex additions fell outside it.
    in_scope = {p.sections[0] for p in placed if p.sections}
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
    added_paragraphs = [n for n in new if id(n) not in matched_new and (n.is_obligation or LIMIT.search(n.text))
                        and n.sections and n.sections[0] in in_scope]
    emit(stage="compare", message=f"{sum(len(c.requirements) for c in cases.values())} requirements on changed "
                                  f"paragraphs, {len(added_paragraphs)} new paragraphs with obligations")

    examples = "\n".join(f"- {r['text']}" for r in old_reqs[:8])

    def kept(r: dict, c: Case) -> str:
        """A hint when much of the requirement's own wording is gone from the new version."""
        before = _coverage(r["text"], c.old.text)
        after = max((_coverage(r["text"], n.text) for n in new), default=0.0)
        return f" [only {after:.0%} of this requirement's wording remains in the new version]" if before - after >= 0.15 else ""

    def decide(batch: list[Case]) -> tuple[list[dict], list[tuple[corpus.Paragraph, str]]]:
        text = "\n\n".join(
            f"CASE {i}\nOLD paragraph {c.old.label}:\n{c.old.text[:2500]}\n"
            "REQUIREMENTS:\n" + "\n".join(f"  {r['id']}: {r['text']}{kept(r, c)}" for r in c.requirements) + "\n"
            + ("NEW candidate paragraphs:\n" + "\n".join(f"  [{n.label}] {n.text[:1500]}" for n in c.candidates)
               if c.candidates else "NEW version: no paragraph contains this content")
            for i, c in enumerate(batch)
        )
        answer = llm.json(model=settings.model_select, system=system(DECIDE_SYSTEM), schema=DECIDE_SCHEMA,
                          effort="medium", content=[{"type": "text", "text": f"Regulation {regulation}.\n\n{text}"}])
        found = []
        for a in answer["additions"]:
            if 0 <= a["case"] < len(batch) and a["text"].strip():
                case = batch[a["case"]]
                target = next((n for n in case.candidates if n.label == a["new_paragraph"].strip().rstrip(".")), case.new)
                if target is not None and target.sections and target.sections[0] in in_scope:
                    found.append((target, a["text"].strip()))
        return answer["decisions"], found

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
        decided = list(decided)
        decisions = {d["requirement_id"]: d for batch, _ in decided for d in batch}
        amended = [x for _, batch in decided for x in batch]  # new obligations inside amended paragraphs
        additions = [x for batch in written for x in batch]

    if flag_all:
        recheck = [(c, r) for c in case_list for r in c.requirements if c.candidates
                   and decisions.get(r["id"], {}).get("status", "unchanged") == "unchanged"]

        def rewrite(batch: list[tuple[Case, dict]]) -> list[dict]:
            text = "\n\n".join(
                f"{r['id']} (old paragraph {c.old.label}): {r['text']}\nOLD paragraph:\n{c.old.text[:2000]}\n"
                "NEW paragraph(s):\n" + "\n".join(f"  [{n.label}] {n.text[:1500]}" for n in c.candidates)
                for c, r in batch)
            return llm.json(model=settings.model_select, system=system(RECHECK_SYSTEM), schema=RECHECK_SCHEMA,
                            effort="medium", content=[{"type": "text", "text": f"Regulation {regulation}.\n\n{text}"}]
                            )["requirements"]

        with ThreadPoolExecutor(settings.parallel) as executor:
            redone = [x for b in executor.map(rewrite, [recheck[i:i + BATCH] for i in range(0, len(recheck), BATCH)])
                      for x in b]
        old_text = {r["id"]: r["text"] for _, r in recheck}
        for x in redone:
            if x["requirement_id"] in old_text and _norm(x["new_text"]) != _norm(old_text[x["requirement_id"]]):
                decisions[x["requirement_id"]] = {"requirement_id": x["requirement_id"], "status": "modified",
                                                  "new_text": x["new_text"], "new_paragraph": x["new_paragraph"]}
        emit(stage="recheck", message=f"{len(recheck)} unchanged requirements re-checked")

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
    # Obligations found inside amended paragraphs (settings.subgoal_amended_additions).
    paired_old = {id(n): o for o in old for n in [pairs[id(o)]] if n is not None}
    for p, text in (amended if settings.subgoal_amended_additions else []):
        old_p = paired_old.get(id(p)) or next((c.old for c in case_list if p in c.candidates), None)
        what = _what_changed(old_p.text if old_p else "", p.text, text)
        changes.append({
            "requirement_id": None, "change": "added", "new_text": text[:2000],
            "reason": f"Paragraph {p.label} of {regulation} was amended in version {new_version}: {what}.",
            "source": {"page": p.page, "paragraph": p.label},
        })
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


def score(changes: list[dict], expected: list[dict]) -> dict:
    """Against a case's expected.json: recall and accuracy of the flagged changes, and the added requirements."""
    want = {c["requirement_id"]: c["change"] for c in expected if c["change"] != "added"}
    ours = {c["requirement_id"]: c["change"] for c in changes if c["change"] != "added"}
    found = set(want) & set(ours)
    added_want = {c["source"]["paragraph"] for c in expected if c["change"] == "added"}
    added_ours = {c["source"]["paragraph"] for c in changes if c["change"] == "added"}
    return {
        "flagged_recall": round(len(found) / max(1, len(want)), 3),
        "flagged_precision": round(len(found) / max(1, len(ours)), 3),
        "change_type_right": f"{sum(want[i] == ours[i] for i in found)} of {len(found)}",
        "missed": sorted(set(want) - set(ours)),
        "added_paragraphs_found": f"{len(added_want & added_ours)} of {len(added_want)}",
    }


def record_path(case: str, flag_all: bool = False) -> Path:
    return settings.runs_dir / "subgoal" / f"{case}{'-recall' if flag_all else ''}.json"


def execute(case: str, emit: Emit = _noop, flag_all: bool | None = None) -> dict:
    """Run a sub-goal case, validate and (when an answer key exists) score it, and save it for the app."""
    from datetime import datetime, timezone

    from certivity import checks

    folder = settings.package_dir / "subgoal" / case
    llm = LLM()
    result = run(folder, llm, emit=emit, flag_all=flag_all)
    old_reqs = json.loads((folder / "requirements_old.json").read_text())
    sub = submission(settings.team_id or "team-id-not-set", f"subgoal-{case}", result)
    expected = folder / "expected.json"
    record = {
        "case": case, "case_id": f"subgoal-{case}", "regulation": result["regulation"],
        "old_version": result["old_version"], "new_version": result["new_version"],
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requirements_old": len(old_reqs), "unplaced": result["unplaced"], "changes": result["changes"],
        "validation": checks.validate(sub, old_reqs),
        "evaluation": score(result["changes"], json.loads(expected.read_text())["changes"]) if expected.is_file() else None,
        "usage": llm.usage.as_dict(),
    }
    path = record_path(case)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1))
    return record
