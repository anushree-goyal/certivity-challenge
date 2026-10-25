"""Specification and/or drawings -> applicable regulations -> cited requirements.

Code does the reading: every base PDF is split into numbered paragraphs, and the ones that state an
obligation become candidates. The model makes three kinds of judgement only:

1. one call (with the drawings) describes the product and decides which regulations in the index apply;
2. one call reads those regulations' tables of contents and drops the sections that concern something else
   (other components of the same system, other vehicle types), so they never reach step 3;
3. batched calls on the small model screen the remaining paragraphs, returning only the ids that may hold a
   requirement for this product (cheap: almost no output), keeping a paragraph when in doubt; two passes with
   different batch boundaries, and a paragraph either pass keeps goes on;
4. batched calls on the small model, with more thinking, read only the kept paragraphs closely, drop the ones
   that clearly do not apply, and write one requirement per obligation; this runs three times;
5. paragraphs all three writing passes kept stay; the few they disagreed on go to Sonnet, which settles them;
6. Sonnet checks each cited paragraph for obligations its requirements miss (for this product only) and adds them.

Nothing here is specific to one kind of part. What is specific to this corpus of regulations lives in the
notes file (CERTIVITY_NOTES), which every prompt includes. Every requirement cites a paragraph the parser
actually found, on the page it was found on.
"""

from __future__ import annotations

import collections
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Callable

from certivity import corpus
from certivity.config import settings
from certivity.llm import LLM, image_block

Emit = Callable[..., None]

# Sections that never carry an engineering obligation for the product itself.
SKIP_HEADINGS = re.compile(
    r"definition|transitional provision|penalt|production definitively|names and addresses|"
    r"communication|information document|information folder|application for approval",
    re.I,
)
MAX_TEXT = 3000

SELECT_SYSTEM = """You are a homologation engineer. Given a product specification and/or drawings of a part, you
describe the product and decide which regulations from a fixed list apply to it.

Decide from each regulation's scope, not only its title: a regulation applies when any part, function, sensor,
lamp, electronic unit or material of the product falls within what its scope covers. A regulation applies if it
sets requirements the product itself must meet: regulations that approve the part,
regulations for the system or vehicle it is fitted to where they set requirements on this part (its design,
performance, markings, tests or installation), and cross-cutting regulations triggered by what the part
contains. Exclude regulations for other systems, other parts and other vehicle types. Be decisive: a regulation
that only might be relevant under assumptions the input does not support stays out.

List every regulation you considered with applies true or false, so a regulation you weigh and reject is
recorded as rejected. In the product description, list the functions and parts the input actually states or shows, and in unknowns the
facts that would change which requirements apply."""

SCOPE_SYSTEM = """You scope regulations for a product. For each regulation you get its table of contents (section
keys and titles) and decide which sections cannot contain requirements for this product.

Put a section key in skip only when its title shows it concerns something else: other components or subsystems,
functions the product does not have, other vehicle types, or the approval authority's own procedure. Skipping a
section skips everything inside it. Keep a section when it might hold a requirement for the product, when its
title is cut off or ambiguous, and when it covers markings the product must bear (these often sit under an
"administrative provisions" heading). Requirements on a component or sub-assembly of the kind the product is
apply to the product.

In focus, say in one or two sentences what in this regulation concerns the product and what does not; the
engineers who read each paragraph next will use it."""

SCREEN_SYSTEM = """You screen regulation paragraphs for a specific product. Return the ids of every paragraph that
may state something this product (its design, performance, markings, materials, tests, or installation) must
meet: an obligation, a restriction or prohibition, a limit value, or a test condition or pass criterion.

When in doubt, keep the paragraph: another engineer reads every kept paragraph closely, and a paragraph you leave
out is lost for good. Leave out only paragraphs that clearly concern another component, function or variant,
the approval authority's own procedure, or that state nothing a product could be checked against. Use the
section path and the focus given for the regulation."""

WRITE_SYSTEM = """You turn screened regulation paragraphs into the engineering requirements a specific product must
fulfil. Each paragraph was screened as possibly relevant; read it closely and keep only what an engineer
designing this product would put on its requirement list.

Drop a paragraph (return no requirements for it) when any of these holds:
- it concerns another component, function, or the system or vehicle as a whole without a link to this product;
- it applies only to a category, class or variant (a vehicle category, a lamp category, a device class) that the
  product is not, according to its description;
- it is a permission that constrains nothing ("may also be used as", "is allowed", "need not"), the approval
  authority's own procedure, or a description or definition;
- it is an introduction or general statement whose details follow in its sub-paragraphs (often a short
  paragraph numbered like "5." or "5.3."): the sub-paragraphs carry the requirements;
- it belongs to a test procedure (typically in an annex) and only says how the laboratory sets up, mounts,
  measures or calculates. Keep from test procedures what the product is tested under and must achieve: test
  levels, test signals, durations, environmental conditions (temperature, humidity, salt mist, irradiation) and
  pass criteria.
Markings the product must bear, restrictions ("may only ... if", "is not permitted") and limit values are
requirements.

Never put the product's name or its part names into a requirement ("the bumper module", "the lamp unit of the
product"): keep the regulation's own subject and scope word for word ("the external surface of the vehicle", "the
ESA", "the device"). The product description only tells you which paragraphs apply.

For each paragraph you keep, write its requirements following the writing guide below: one requirement per
distinct obligation (each line of a list or table is shown on its own line), each one standalone, with its
exceptions, conditions and values kept inside it. Where the guide and these instructions differ, follow the guide."""

SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "product": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "vehicle_categories": {"type": "array", "items": {"type": "string"}},
                "functions": {"type": "array", "items": {"type": "string"}},
                "parts": {"type": "array", "items": {"type": "string"}},
                "materials": {"type": "array", "items": {"type": "string"}},
                "unknowns": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "vehicle_categories", "functions", "parts", "materials", "unknowns"],
            "additionalProperties": False,
        },
        "applicable": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"regulation": {"type": "string"}, "applies": {"type": "boolean"},
                               "reason": {"type": "string"}},
                "required": ["regulation", "applies", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["product", "applicable"],
    "additionalProperties": False,
}

SCOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "regulations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "regulation": {"type": "string"},
                    "focus": {"type": "string"},
                    "skip": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["regulation", "focus", "skip"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["regulations"],
    "additionalProperties": False,
}

SCREEN_SCHEMA = {
    "type": "object",
    "properties": {"keep": {"type": "array", "items": {"type": "string"}}},
    "required": ["keep"],
    "additionalProperties": False,
}

_RULES = WRITE_SYSTEM[WRITE_SYSTEM.index("Drop a paragraph"):WRITE_SYSTEM.index("For each paragraph you keep")]
ARBITER_SYSTEM = (
    "You are the senior reviewer who settles disputed cases. Engineers disagreed on whether each of these regulation "
    "paragraphs belongs on the requirement list for a specific product. Decide each one strictly: a paragraph belongs "
    "only if an engineer designing this product would have to design or test for it.\n\n"
    + _RULES.replace("Drop a paragraph (return no requirements for it) when any of these holds:",
                     "It does not belong when any of these holds:")
    + "\nAnswer for every id, in order."
)

GAPFILL_SYSTEM = (
    "You check a requirement list for completeness. For each regulation paragraph you get the requirements already "
    "written from it. List only the obligations the paragraph states that none of the existing requirements covers: a "
    "missing case, row, limit, condition-specific rule or separate duty. Do not restate, rephrase or merge existing "
    "requirements, and do not add anything the paragraph does not state. Most paragraphs are already complete: return "
    "no additions for them. Only add an obligation that applies to this product as described: skip cases, rows and "
    "rules for functions, lamp or device types, categories or variants the product does not have. Never put the "
    "product's name or its part names into an addition: keep the regulation's own subject and scope word for word. "
    "Write each addition following the writing guide below."
)

GAPFILL_SCHEMA = {
    "type": "object",
    "properties": {
        "paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "additions": {"type": "array", "items": {"type": "string"}}},
                "required": ["id", "additions"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["paragraphs"],
    "additionalProperties": False,
}

ARBITER_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "applies": {"type": "boolean"}},
                "required": ["id", "applies"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}

WRITE_SCHEMA = {
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
class Requirement:
    id: str
    text: str
    paragraph: corpus.Paragraph

    def submission(self) -> dict:
        p = self.paragraph
        return {"id": self.id, "text": self.text[:2000],
                "source": {"regulation": p.regulation, "version": p.version, "page": p.page, "paragraph": p.label}}


class Cancelled(Exception):
    pass


def _noop(**_: object) -> None:
    pass


@cache
def notes() -> str:
    path = settings.notes
    return path.read_text().strip() if path.is_file() else ""


@cache
def style() -> str:
    path = settings.style
    return path.read_text().strip() if path.is_file() else ""


def system(prompt: str, with_style: bool = False) -> str:
    """The prompt plus the corpus notes; writing and arbitration also get the house style guide."""
    return "\n\n".join(part for part in (prompt, notes(), style() if with_style else "") if part)


def select_regulations(llm: LLM, spec_text: str, drawings: list[Path]) -> dict:
    """Asked twice, the second time with the list in reverse order, so the two answers are independent; a
    regulation either answer picks is kept. Missing a whole regulation costs every requirement in it."""
    docs = corpus.documents()
    available = [d for d in docs.values() if d.pdf]
    answers = []
    scopes = {d.regulation: corpus.scope_text(d.regulation) for d in available}
    for order in (available, available[::-1]):
        listing = "\n".join(f"- {d.regulation}: {d.title}\n  Scope: {scopes[d.regulation] or '-'}" for d in order)
        content: list[dict] = [image_block(p) for p in drawings]
        content.append({"type": "text", "text":
            f"Regulations you may choose from:\n{listing}\n\n"
            f"Product specification:\n{spec_text.strip() or '(none - use the drawings)'}"})
        answers.append(llm.json(model=settings.model_select, system=system(SELECT_SYSTEM), content=content,
                                schema=SELECT_SCHEMA, effort="medium"))
    chosen: dict[str, str] = {}
    for answer in answers:
        for a in answer["applicable"]:
            if a["applies"] and a["regulation"] in docs and docs[a["regulation"]].pdf:
                chosen.setdefault(a["regulation"], a["reason"])
    order = {d.regulation: i for i, d in enumerate(available)}
    return {"product": answers[0]["product"],
            "applicable": [{"regulation": r, "reason": chosen[r]} for r in sorted(chosen, key=order.get)]}


def candidates(regulations: list[str]) -> dict[str, list[corpus.Paragraph]]:
    return {r: [p for p in corpus.paragraphs(r) if p.is_obligation and not SKIP_HEADINGS.search(p.heading)]
            for r in regulations}


def scope(llm: LLM, brief: str, pools: dict[str, list[corpus.Paragraph]]) -> dict[str, dict]:
    """{regulation: {"focus": str, "skip": [section keys]}} from one call over the tables of contents."""
    outlines = "\n\n".join(
        f"{regulation}:\n" + "\n".join(f"  [{key}] {title}" for key, title in corpus.outline(pool))
        for regulation, pool in pools.items() if pool
    )
    if not outlines:
        return {}
    answer = llm.json(model=settings.model_select, system=system(SCOPE_SYSTEM), schema=SCOPE_SCHEMA, content=[
        {"type": "text", "text": f"Product:\n{brief}\n\nTables of contents:\n\n{outlines}"}])
    return {r["regulation"]: r for r in answer["regulations"] if r["regulation"] in pools}


def product_brief(product: dict) -> str:
    return (f"{product['summary']}\nVehicle categories: {', '.join(product['vehicle_categories']) or 'unknown'}\n"
            f"Functions: {'; '.join(product['functions'])}\nParts: {'; '.join(product['parts'])}\n"
            f"Materials: {'; '.join(product.get('materials', [])) or 'not stated'}")


def _listing(batch: list[corpus.Paragraph]) -> tuple[dict[str, corpus.Paragraph], str]:
    by_key = {f"P{i}": p for i, p in enumerate(batch)}
    listing = "\n\n".join(
        f"[{key}] {p.regulation} paragraph {p.label} (section: {p.heading or '-'})\n{p.text[:MAX_TEXT]}"
        for key, p in by_key.items()
    )
    return by_key, listing


def _prompt(brief: str, focus: str, regulation: str, listing: str) -> list[dict]:
    return [{"type": "text", "text": f"Product:\n{brief}\n\nWhat in {regulation} concerns this product: {focus or '-'}\n\n"
                                     f"Paragraphs:\n\n{listing}"}]


def screen_batch(llm: LLM, brief: str, focus: str, batch: list[corpus.Paragraph]) -> list[corpus.Paragraph]:
    """The paragraphs that may hold a requirement for the product. Ids only, so the output costs almost nothing."""
    by_key, listing = _listing(batch)
    answer = llm.json(model=settings.model_filter, system=system(SCREEN_SYSTEM), schema=SCREEN_SCHEMA,
                      content=_prompt(brief, focus, batch[0].regulation, listing))
    keep = set(answer["keep"])
    return [p for key, p in by_key.items() if key in keep]  # an id the model invented matches nothing


def _label_key(label: str) -> tuple:
    parts = label.split()
    return (parts[0] == "Annex", *[p for p in parts if not p[0].isdigit()][1:],
            *[int(x) for x in parts[-1].split(".") if x.isdigit()])


def worked_examples(batch: list[corpus.Paragraph], limit: int = 6) -> str:
    """Reviewed paragraph -> requirement pairs from the library, from the same regulation version and nearest by
    paragraph number, never for a paragraph in this batch: how an expert words and splits such paragraphs."""
    if not settings.examples:
        return ""
    first = batch[0]
    library = {k: v for k, v in reviewed_library().items()
               if k[0] == first.regulation and k[1] == first.version and k[2] not in {p.label for p in batch}}
    if not library:
        return ""
    paragraphs = {p.label: p for p in corpus.paragraphs(first.regulation)}
    target = _label_key(first.label)

    def distance(label: str) -> tuple:
        key = _label_key(label)
        shared = next((i for i, (a, b) in enumerate(zip(key, target)) if a != b), min(len(key), len(target)))
        return (-shared, abs(len(key) - len(target)))

    chosen = sorted((k for k in library if k[2] in paragraphs), key=lambda k: distance(k[2]))[:limit]
    blocks = [f"Paragraph {k[2]}: {' '.join(paragraphs[k[2]].text.split())[:700]}\n"
              + "\n".join(f"  -> {t}" for t in library[k]) for k in chosen]
    return "Reviewed examples (paragraph -> the requirements an expert wrote from it). Follow their wording, " \
           "subject and splitting:\n\n" + "\n\n".join(blocks) + "\n\n"


def write_batch(llm: LLM, brief: str, focus: str, batch: list[corpus.Paragraph]) -> list[tuple[corpus.Paragraph, str]]:
    by_key, listing = _listing(batch)
    content = _prompt(brief, focus, batch[0].regulation, listing)
    content[0]["text"] = worked_examples(batch) + content[0]["text"]
    answer = llm.json(model=settings.model_write, system=system(WRITE_SYSTEM, with_style=True), schema=WRITE_SCHEMA,
                      effort=settings.write_effort, content=content)
    out = []
    for item in answer["paragraphs"]:
        paragraph = by_key.get(item["id"])
        if paragraph:  # an id the model invented is dropped
            out += [(paragraph, t.strip()) for t in item["requirements"] if t.strip()]
    return out


def arbitrate_batch(llm: LLM, brief: str, focus: str, batch: list[corpus.Paragraph]) -> list[corpus.Paragraph]:
    """The disputed paragraphs the stronger model says belong on the product's requirement list."""
    by_key, listing = _listing(batch)
    answer = llm.json(model=settings.model_arbiter, system=system(ARBITER_SYSTEM, with_style=True), schema=ARBITER_SCHEMA,
                      effort="medium", content=_prompt(brief, focus, batch[0].regulation, listing))
    return [by_key[d["id"]] for d in answer["decisions"] if d["applies"] and d["id"] in by_key]


def gapfill_batch(llm: LLM, brief: str,
                  batch: list[tuple[corpus.Paragraph, list[str]]]) -> list[tuple[corpus.Paragraph, str]]:
    """Obligations each paragraph states that its existing requirements miss. Only adds; never changes a requirement."""
    text = "\n\n".join(
        f"[P{i}] {p.regulation} paragraph {p.label} (section: {p.heading or '-'})\n{p.text[:MAX_TEXT]}\nEXISTING:\n"
        + "\n".join(f"  - {t}" for t in texts)
        for i, (p, texts) in enumerate(batch)
    )
    answer = llm.json(model=settings.model_gapfill, system=system(GAPFILL_SYSTEM, with_style=True),
                      schema=GAPFILL_SCHEMA, effort="medium",
                      content=[{"type": "text", "text": f"Product:\n{brief}\n\n{text}"}])
    out = []
    for item in answer["paragraphs"]:
        index = item["id"].strip("[]P ")
        if index.isdigit() and int(index) < len(batch):  # an id the model invented is dropped
            out += [(batch[int(index)][0], t.strip()) for t in item["additions"] if t.strip()]
    return out


def _offset(n: int, passes: int, size: int, variant: int) -> int:
    """Batch start offset for pass n; a later ensemble variant shifts every boundary so its calls differ."""
    return (n * size // passes + variant * size // (2 * passes)) % size


def _in_batches(pools: dict[str, list[corpus.Paragraph]], size: int,
                offset: int = 0) -> list[tuple[str, list[corpus.Paragraph]]]:
    """Batches never mix regulations, so each carries its regulation's focus. A non-zero offset makes the first
    batch of each regulation shorter, so the batch boundaries (and each paragraph's neighbours) move."""
    out = []
    for r, pool in pools.items():
        cuts = [0, *range(offset or size, len(pool), size), len(pool)]
        out += [(r, pool[a:b]) for a, b in zip(cuts, cuts[1:]) if b > a]
    return out


def _parallel(stage: str, message: str, work: Callable, batches: list, emit: Emit,
              cancelled: Callable[[], bool], warnings: list[str]) -> list:
    """Run work(regulation, batch) for every batch; a failed batch costs its paragraphs, not the run."""
    results: list = [[] for _ in batches]
    emit(stage=stage, message=message, completed=0, total=len(batches))
    with ThreadPoolExecutor(settings.parallel) as executor:
        futures = [executor.submit(work, r, b) for r, b in batches]
        for i, future in enumerate(futures):
            if cancelled():
                executor.shutdown(cancel_futures=True)
                raise Cancelled
            try:
                results[i] = future.result()
            except Exception as exc:
                b = batches[i][1]
                warnings.append(f"{b[0].regulation} {b[0].label} to {b[-1].label}: not {stage} ({exc})")
            emit(stage=stage, message=message, completed=i + 1, total=len(batches))
    return results


@cache
def reviewed_library() -> dict[tuple[str, str, str], list[str]]:
    """(regulation, version, paragraph) -> the requirements an expert wrote from that paragraph."""
    library: dict[tuple[str, str, str], list[str]] = {}
    for path in sorted((settings.package_dir / "cases").glob("*/expected.json")):
        for r in json.loads(path.read_text()).get("requirements", []):
            s = r["source"]
            key = (s["regulation"], s.get("version", ""), str(s.get("paragraph", "")).strip().rstrip("."))
            library.setdefault(key, []).append(r["text"])
    return library


def apply_library(pairs: list[tuple[corpus.Paragraph, str]]) -> list[tuple[corpus.Paragraph, str]]:
    """Requirements of paragraphs the pipeline selected, with an expert's wording where a reviewed one exists.
    The pipeline still decides which paragraphs apply; the library only words them."""
    library = reviewed_library()
    out, done = [], set()
    for p, text in pairs:
        reviewed = library.get((p.regulation, p.version, p.label))
        if reviewed is None:
            out.append((p, text))
        elif id(p) not in done:
            done.add(id(p))
            out += [(p, t) for t in reviewed]
    return out


def run(spec_text: str, drawings: list[Path], llm: LLM, emit: Emit = _noop,
        cancelled: Callable[[], bool] = lambda: False, write_passes: int | None = None) -> dict:
    """settings.ensemble independent passes over the paragraphs, combined: the first pass is kept in full and each
    later one only adds requirements for paragraphs the earlier ones did not cite. Each pass misses different
    expected requirements; on the dev case two passes raised recall 2-4 points for about 2 points of precision."""
    total = max(1, settings.ensemble)
    out = None
    for variant in range(total):
        def tagged(**e):
            if total > 1 and "message" in e:
                e["message"] = f"pass {variant + 1}/{total}: {e['message']}"
            emit(**e)
        result = _run_once(spec_text, drawings, llm, tagged, cancelled, write_passes, variant)
        if out is None:
            out = result
            continue
        cited = {(r.paragraph.regulation, r.paragraph.label) for r in out["requirements"]}
        out["requirements"] += [r for r in result["requirements"]
                                if (r.paragraph.regulation, r.paragraph.label) not in cited]
        out["warnings"] += result["warnings"]
    out["requirements"] = [Requirement(f"R-{n:04d}", r.text, r.paragraph)
                           for n, r in enumerate(out["requirements"], start=1)]
    return out


def _run_once(spec_text: str, drawings: list[Path], llm: LLM, emit: Emit, cancelled: Callable[[], bool],
              write_passes: int | None, variant: int) -> dict:
    write_passes = write_passes or settings.write_passes
    emit(stage="regulations", message="Reading the input and choosing regulations")
    selection = select_regulations(llm, spec_text, drawings)
    chosen = [a["regulation"] for a in selection["applicable"]]
    emit(stage="regulations", message=f"Applicable: {', '.join(chosen) or 'none'}")
    if cancelled():
        raise Cancelled

    emit(stage="scope", message="Choosing the sections that concern the product")
    brief = product_brief(selection["product"])
    pools = candidates(chosen)
    scopes = scope(llm, brief, pools)
    for regulation, pool in pools.items():
        skip = set(scopes.get(regulation, {}).get("skip", []))
        pools[regulation] = [p for p in pool if not skip & set(p.sections)]
    candidates_total = sum(len(pool) for pool in pools.values())
    if cancelled():
        raise Cancelled

    focus = {r: scopes.get(r, {}).get("focus", "") for r in pools}
    warnings: list[str] = []
    # Each screening pass cuts the batches at different points; a paragraph kept by any pass goes on to writing.
    # Screening returns ids only, so an extra pass costs little and recovers paragraphs one pass dropped.
    batches = [b for n in range(settings.screen_passes)
               for b in _in_batches(pools, settings.screen_batch,
                                    offset=_offset(n, settings.screen_passes, settings.screen_batch, variant))]
    screened = _parallel("screening", f"Screening {candidates_total} obligation paragraphs "
                                      f"({settings.screen_passes} pass{'es' if settings.screen_passes > 1 else ''})",
                         lambda r, b: screen_batch(llm, brief, focus[r], b), batches, emit, cancelled, warnings)
    keep = {id(p) for paragraphs in screened for p in paragraphs}
    kept = {r: [p for p in pool if id(p) in keep] for r, pool in pools.items()}
    # The writing pass runs write_passes times with different batch boundaries, and a paragraph's requirements are
    # kept when at least write_votes passes keep it. One vote (the union) recovers paragraphs a single noisy read
    # drops; more votes trade recall for precision.
    passes = [_in_batches(kept, settings.write_batch, offset=_offset(n, write_passes, settings.write_batch, variant))
              for n in range(write_passes)]
    written = _parallel("writing", f"Writing requirements from {sum(map(len, kept.values()))} paragraphs",
                        lambda r, b: write_batch(llm, brief, focus[r], b),
                        [b for batches in passes for b in batches], emit, cancelled, warnings)
    by_pass, start = [], 0
    for batches in passes:
        by_pass.append([x for batch in written[start:start + len(batches)] for x in batch])
        start += len(batches)
    votes = collections.Counter(pid for items in by_pass for pid in {id(p) for p, _ in items})
    if settings.model_arbiter and len(by_pass) > 1:
        # Paragraphs every pass kept stay. Paragraphs the passes disagreed on go to a stronger model, which sees
        # only these few, so the judgement that decides precision and recall is paid for only where it is needed.
        agreed = {pid for pid, n in votes.items() if n == len(by_pass)}
        disputed = {r: [p for p in pool if 0 < votes[id(p)] < len(by_pass)] for r, pool in kept.items()}
        upheld = _parallel("arbitration", f"Settling {sum(map(len, disputed.values()))} disputed paragraphs",
                           lambda r, b: arbitrate_batch(llm, brief, focus[r], b),
                           _in_batches(disputed, settings.arbiter_batch), emit, cancelled, warnings)
        agreed |= {id(p) for paragraphs in upheld for p in paragraphs}
    else:
        agreed = {pid for pid, n in votes.items() if n >= min(settings.write_votes, len(by_pass))}
    texts: dict[int, list[tuple[corpus.Paragraph, str]]] = {}
    for items in by_pass:  # each paragraph's wording comes from the first pass that kept it
        for pid in {id(p) for p, _ in items} - texts.keys():
            texts[pid] = [(p, t) for p, t in items if id(p) == pid]

    seen: set[str] = set()
    pairs = []
    order = {id(p): i for i, p in enumerate(p for pool in kept.values() for p in pool)}
    for p, text in (x for pid in sorted(agreed, key=order.get) for x in texts[pid]):
        key = " ".join(re.sub(r"[^\w ]", " ", text.lower()).split())
        if id(p) in agreed and key not in seen:  # the same sentence cited from two paragraphs is kept once
            seen.add(key)
            pairs.append((p, text))

    if settings.model_gapfill and pairs:
        # Completeness check on the cited paragraphs: a stronger model lists obligations a paragraph states that its
        # requirements miss, for this product only. Additions go right after their paragraph's requirements.
        cited: dict[int, tuple[corpus.Paragraph, list[str]]] = {}
        for p, text in pairs:
            cited.setdefault(id(p), (p, []))[1].append(text)
        groups = list(cited.values())
        size = settings.gapfill_batch
        found = _parallel("completeness", f"Checking {len(groups)} cited paragraphs for missed obligations",
                          lambda r, b: gapfill_batch(llm, brief, b),
                          [("", groups[i:i + size]) for i in range(0, len(groups), size)], emit, cancelled, warnings)
        additions: dict[int, list[str]] = collections.defaultdict(list)
        for p, text in (x for batch in found for x in batch):
            key = " ".join(re.sub(r"[^\w ]", " ", text.lower()).split())
            if key not in seen:
                seen.add(key)
                additions[id(p)].append(text)
        pairs = [x for p, texts in groups for x in [(p, t) for t in texts + additions[id(p)]]]

    if settings.library:
        pairs = apply_library(pairs)
    requirements = [Requirement(f"R-{n:04d}", text, p) for n, (p, text) in enumerate(pairs, start=1)]
    applicable = [{**a, "focus": scopes.get(a["regulation"], {}).get("focus", ""),
                   "skipped_sections": scopes.get(a["regulation"], {}).get("skip", [])}
                  for a in selection["applicable"]]
    return {
        "product": selection["product"],
        "applicable": applicable,
        "candidates": candidates_total,
        "requirements": requirements,
        "warnings": warnings,
    }


def submission(team_id: str, case_id: str, requirements: list[Requirement]) -> dict:
    return {"team_id": team_id, "case_id": case_id, "requirements": [r.submission() for r in requirements]}
