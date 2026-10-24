"""Economy mode: the whole extraction without a single model call (owner: Anushree).

Used when the model cannot be called - no credit, no key - or when a run is
configured to spend nothing. Every stage keeps its shape and its honesty
rules; only the decision-maker changes:

* Part category: matched against the known categories by word overlap. A known
  category replays the applicability decisions and section-triage verdicts an
  earlier model run recorded, which is the best relevance available for free.
* Applicability and section relevance for a part never seen before: the
  specification's vocabulary compared with each regulation's title and scope
  text, and with each section's heading.
* Requirements: one per paragraph that states an obligation, cited to that
  paragraph's own passage, with the wording turned from legal drafting into
  plain English by fixed rules that never touch a value, unit or defined term.

Every output says it was produced this way, so a reader can tell a
model-reviewed requirement from a rule-derived one.
"""

from __future__ import annotations

import dataclasses
import math
import re
from collections import Counter
from typing import Iterable, Optional

from certivity.contracts import (
    ApplicabilityDecision,
    ApplicabilityStatus,
    Document,
    DocumentRole,
    DraftRequirement,
    NumericValue,
    ObligationKind,
    ProductProfile,
    SourcePassage,
)
from certivity.agent.categories import PartCategory, PartCategoryRegistry
from certivity.agent.context import RunContext
from certivity.agent.extract import SectionResult, draft_id_for
from certivity.agent.plan import PlanItem

ECONOMY_NOTE = (
    "Extracted without a model call: one requirement per obligation paragraph, "
    "worded in plain English by fixed rules."
)

FALLBACK_MODES = ("auto", "lite")
"""Modes that carry on without the model once it is unusable, instead of failing."""

MAX_TEXT_CHARS = 1900
"""Below the official 2,000-character cap, with room for the context prefix."""

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

_WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")

_STOPWORDS = frozenset(
    """
    the and for with that this from are was were has have had not but all any each
    its their there these those which who whom whose what when where while into onto
    upon than then them they will would shall should may might must can could also
    such other only same more most less least very per via one two three four five
    being been does did done use used using within without between under over above
    below after before about against during including include includes included
    shown table figure annex part paragraph regulation regulations vehicle vehicles
    own due out off new set see non yes how why now too few its our you his her
    """.split()
)

_GENERIC_HEADING_TERMS = frozenset(
    """
    general requirement requirements specification specifications provision provisions
    definition definitions test tests testing procedure procedures marking markings
    approval approvals conformity production scope application applications intensity
    intensities luminous colour colours color light emitted emission measurement
    measurements visibility geometric angle angles installation electrical connection
    connections condition conditions device devices lamp lamps system systems
    component components unit units type types category categories class classes
    minimum maximum area surface apparent distribution standard photometric
    performance characteristic characteristics design construction material materials
    durability resistance heat temperature voltage failure failures mode modes
    operation operating source sources other additional appendix model form
    number numbers value values method methods sample samples document documentation
    """.split()
)
"""Heading words that describe *what kind of rule* a section holds, not which
device it is about. A heading word outside this list and outside the product's
own vocabulary usually names a different device ("fog", "front", "indicator")."""


def _stem(word: str) -> str:
    if len(word) > 6 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def terms(text: Optional[str]) -> set[str]:
    """Content words, stemmed. A hyphenated word also counts as its parts, so
    "retro-reflector" meets "retro-reflective" and "light-signalling" meets "light"."""
    out: set[str] = set()
    for word in _WORD.findall((text or "").lower()):
        for part in (word, *word.split("-")):
            if part not in _STOPWORDS and len(part) > 2:
                out.add(_stem(part))
    return out


def product_terms(profile: ProductProfile, specification: Optional[str]) -> set[str]:
    parts = [specification or "", profile.product_type or "", profile.summary or ""]
    for group in (profile.facts, profile.components, profile.functions, profile.materials):
        parts.extend(f"{f.key.replace('_', ' ')} {f.value or ''}" for f in group)
    return terms(" ".join(parts))


# --------------------------------------------------------------------------- #
# Product profile
# --------------------------------------------------------------------------- #

_VEHICLE_CATEGORY = re.compile(r"\b([MNOL][1-7][A-Z]?|L[1-7]e)\b")


def economy_profile(specification: Optional[str]) -> ProductProfile:
    """What the specification text itself says, without interpretation."""
    text = (specification or "").strip()
    first = next((line.strip("# ").strip() for line in text.splitlines() if line.strip()), "")
    first = re.sub(r"^(spec(ification)?|product)\s*:\s*", "", first, flags=re.IGNORECASE)
    summary = re.sub(r"\s+", " ", text)[:400] or None
    categories = tuple(dict.fromkeys(_VEHICLE_CATEGORY.findall(text)))
    return ProductProfile(
        product_type=first[:120] or None,
        vehicle_categories=categories,
        unknowns=("product facts were not interpreted by a model (economy mode)",),
        summary=summary,
    )


# --------------------------------------------------------------------------- #
# Part category
# --------------------------------------------------------------------------- #


def match_category(
    ctx: RunContext,
    registry: PartCategoryRegistry,
    profile: ProductProfile,
    specification: Optional[str],
) -> Optional[PartCategory]:
    """Best known category by vocabulary overlap; a new entry when nothing fits."""
    vocabulary = product_terms(profile, specification)
    best: tuple[float, Optional[dict]] = (0.0, None)
    for entry in registry.categories():
        # A category matches when one of its names is fully present in the
        # specification ("rear lamp"), not when every alias is.
        names = [entry["label"], *entry["aliases"]]
        name_hit = max(
            (len(t & vocabulary) / len(t) for t in map(terms, names) if t), default=0.0
        )
        description_terms = terms(entry["description"])
        description_hit = (
            len(description_terms & vocabulary) / len(description_terms) if description_terms else 0
        )
        score = 0.7 * name_hit + 0.3 * description_hit
        if score > best[0]:
            best = (score, entry)

    score, entry = best
    if entry is not None and score >= 0.6:
        registry.ensure(entry["slug"], entry["label"], entry["description"])
        ctx.event(
            "part_category",
            f"Part category: {entry['label']} (known, matched by wording, score {score:.2f})",
            affected_ids=[entry["slug"]],
        )
        return PartCategory(
            slug=entry["slug"],
            label=entry["label"],
            description=entry["description"],
            is_new=False,
            reason=f"economy mode: vocabulary overlap {score:.2f}",
        )

    label = (profile.product_type or "unnamed part")[:80]
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "unnamed-part"
    created = registry.ensure(slug, label, (profile.summary or "")[:200])
    ctx.event(
        "part_category",
        f"Part category: {label} ({'new, added to the list' if created else 'known'}; "
        "no earlier model knowledge to reuse)",
        affected_ids=[slug],
    )
    return PartCategory(slug=slug, label=label, description=profile.summary or "", is_new=created)


def _shared_stem(a: str, b: str) -> bool:
    """"reflective" ~ "reflector", "indicator" ~ "indication"; not "position" ~ "positive"."""
    shorter = min(len(a), len(b))
    if shorter < 6:
        return False
    n = 0
    while n < shorter and a[n] == b[n]:
        n += 1
    return n >= 6 and n >= 0.75 * shorter and n >= shorter - 2


def _covered(term: str, vocabulary: set[str]) -> bool:
    return term in vocabulary or any(_shared_stem(term, v) for v in vocabulary)


_SUPER_GENERIC = frozenset({"device", "system", "vehicle", "unit", "type", "component", "installation"})
_TITLE_VEHICLE_CATEGORY = re.compile(r"\b([MNOL][1-7])\b")


def foreign_title_terms(
    document: Document, vocabulary: set[str], vehicle_categories: Iterable[str] = ()
) -> list[str]:
    """Title words naming something the specification never mentions.

    Only meaningful when the title shares at least one word with the
    specification: "Rear fog lamps" against a rear lamp without a fog
    function. A title in another domain entirely ("Electromagnetic
    compatibility") says nothing either way and returns no foreign terms.
    """
    raw_title = str((document.index_entry or {}).get("title") or "")
    title = re.sub(r"\([^)]*\)", " ", raw_title)
    product_categories = {c[:2].upper() for c in vehicle_categories}
    title_categories = set(_TITLE_VEHICLE_CATEGORY.findall(raw_title))
    if product_categories and title_categories and not title_categories & product_categories:
        # "... on L3 vehicles" for an M1 product: a different kind of vehicle.
        return sorted(title_categories)
    # An acronym the specification also uses ("(LED)") names the same thing.
    acronyms = {a.lower() for a in re.findall(r"\(([A-Z0-9]{2,6})\)", raw_title)}
    if acronyms & vocabulary:
        return []
    # A list title ("Position, stop and end-outline lamps") covers the product
    # when any one of its items does.
    items = [i for i in re.split(r",|\band\b|\bor\b|/", title) if i.strip()]
    if len(items) > 1:
        for piece in items:
            piece_terms = {t for t in terms(piece) if t not in _GENERIC_HEADING_TERMS}
            if piece_terms and all(_covered(t, vocabulary) for t in piece_terms):
                return []
    all_terms = terms(title)
    distinctive = {t for t in all_terms if t not in _GENERIC_HEADING_TERMS}
    # A hyphenated word whose parts are all accounted for adds nothing of its own.
    distinctive = {
        t for t in distinctive
        if "-" not in t
        or not all(_covered(_stem(p), vocabulary) or _stem(p) in _GENERIC_HEADING_TERMS
                   for p in t.split("-") if len(p) > 2)
    }
    # Same domain when any title word other than the most generic ones is shared
    # ("light" in "Adaptive front lighting systems" for a rear lamp).
    if not distinctive or not any(
        _covered(t, vocabulary) for t in all_terms if t not in _SUPER_GENERIC
    ):
        return []
    return sorted(t for t in distinctive if not _covered(t, vocabulary))


def prune_uncertain(
    ctx: RunContext,
    decisions: list[ApplicabilityDecision],
    selected: list[Document],
    vocabulary: set[str],
    vehicle_categories: Iterable[str] = (),
) -> tuple[list[ApplicabilityDecision], list[Document]]:
    """Drop *uncertain* regulations whose title names a device the product lacks.

    A model keeps such a regulation as uncertain because the specification does
    not say the product lacks that function; reading the whole regulation for a
    function the specification never names is what economy mode cannot afford.
    Applicable regulations - older versions included - are never touched.
    """
    by_id = {d.document_id: d for d in selected}
    kept_decisions: list[ApplicabilityDecision] = []
    dropped: list[str] = []
    for decision in decisions:
        if decision.status is ApplicabilityStatus.UNCERTAIN:
            docs = [by_id[i] for i in decision.document_ids if i in by_id]
            foreign = sorted({
                t for d in docs for t in foreign_title_terms(d, vocabulary, vehicle_categories)
            })
            if foreign:
                dropped.append(decision.regulation)
                decision = dataclasses.replace(
                    decision,
                    status=ApplicabilityStatus.EXCLUDED,
                    explanation=(
                        f"Economy mode: not read - the title names {', '.join(foreign)}, which "
                        f"the specification never mentions. {decision.explanation}"
                    ),
                )
        kept_decisions.append(decision)
    if dropped:
        ctx.event("applicability", "Not reading " + ", ".join(dropped)
                  + " (titles name devices the specification never mentions)")
    keep_ids = {
        i for d in kept_decisions if d.status is not ApplicabilityStatus.EXCLUDED for i in d.document_ids
    }
    return kept_decisions, [d for d in selected if d.document_id in keep_ids]


# --------------------------------------------------------------------------- #
# Applicability for a part never seen before
# --------------------------------------------------------------------------- #


def economy_applicability(
    ctx: RunContext, vocabulary: set[str]
) -> tuple[list[ApplicabilityDecision], list[Document]]:
    """Regulations whose title and scope share the product's distinctive words."""
    documents = list(ctx.corpus.list_documents(DocumentRole.BASE))
    by_regulation: dict[str, list[Document]] = {}
    for document in documents:
        by_regulation.setdefault(document.regulation, []).append(document)

    title_terms: dict[str, set[str]] = {}
    scope_terms: dict[str, set[str]] = {}
    for regulation, docs in by_regulation.items():
        # The index entry carries the subject ("Light-Signalling Devices"); the
        # document title is only "UN R148, version 1.6.0".
        titles = [str((d.index_entry or {}).get("title") or "") for d in docs] + [d.title for d in docs]
        title_terms[regulation] = terms(" ".join(titles))
        scope_text = []
        for d in docs:
            try:
                scope_text.extend(p.text for p in ctx.corpus.read_scope(d.document_id))
            except Exception:
                pass
        scope_terms[regulation] = terms(" ".join(scope_text)[:20000])

    frequency = Counter(t for regulation in by_regulation for t in title_terms[regulation] | scope_terms[regulation])
    total = len(by_regulation)

    def idf(term: str) -> float:
        return math.log((1 + total) / (1 + frequency[term]))

    scores: dict[str, tuple[float, list[str]]] = {}
    for regulation in by_regulation:
        shared_title = vocabulary & title_terms[regulation]
        shared_scope = vocabulary & scope_terms[regulation]
        # The title names the regulation's subject; scope text is long and shares
        # common words with almost anything, so it only breaks ties.
        score = 3 * sum(idf(t) for t in shared_title) + 0.3 * sum(idf(t) for t in shared_scope)
        scores[regulation] = (score, sorted(shared_title | shared_scope, key=idf, reverse=True)[:8])

    top = max((s for s, _ in scores.values()), default=0.0)
    decisions: list[ApplicabilityDecision] = []
    for regulation in sorted(by_regulation):
        score, shared = scores[regulation]
        docs = by_regulation[regulation]
        selected = top > 0 and score >= 0.35 * top
        decisions.append(
            ApplicabilityDecision(
                regulation=regulation,
                version_label=docs[0].version_label,
                document_ids=tuple(d.document_id for d in docs),
                status=ApplicabilityStatus.UNCERTAIN if selected else ApplicabilityStatus.EXCLUDED,
                explanation=(
                    f"Economy mode (no model): title and scope share the product's words "
                    f"{', '.join(shared) or 'none'} (score {score:.1f} of best {top:.1f}). "
                    + ("Kept for inspection." if selected else "Below the selection threshold.")
                ),
            )
        )
    kept = [d for d in decisions if d.status is not ApplicabilityStatus.EXCLUDED]
    ctx.event(
        "applicability",
        "Economy applicability: " + ", ".join(d.regulation for d in kept),
        completed=len(decisions),
        total=len(decisions),
    )
    selected_docs = [doc for d in kept for doc in by_regulation[d.regulation]]
    return decisions, selected_docs


def device_vocabulary(ctx: RunContext) -> set[str]:
    """Words that name a kind of device or system somewhere in the regulation index.

    Section headings are full of ordinary words ("means", "case", "mark"); only a
    word some regulation's title uses for its subject ("fog", "indicator",
    "mirror", "front") is evidence that a heading is about another device.
    """
    words: set[str] = set()
    for document in ctx.corpus.list_documents(DocumentRole.BASE):
        title = re.sub(r"\([^)]*\)", " ", str((document.index_entry or {}).get("title") or ""))
        words |= {t for t in terms(title) if t not in _GENERIC_HEADING_TERMS and "-" not in t}
    return words


def economy_section_verdicts(
    ctx: RunContext, plan: list[PlanItem], vocabulary: set[str]
) -> dict[str, dict[str, str]]:
    """Skip a section whose heading names a device the product does not mention."""
    devices = device_vocabulary(ctx)
    verdicts: dict[str, dict[str, str]] = {}
    for item in plan:
        heading = item.section.heading or ""
        foreign = sorted(
            t for t in terms(heading)
            if t in devices and not _covered(t, vocabulary)
        )
        if foreign:
            verdicts[item.section_id] = {
                "verdict": "skip",
                "reason": f"heading names {', '.join(foreign)}, which the specification never mentions",
            }
        else:
            verdicts[item.section_id] = {"verdict": "extract", "reason": "heading fits the product"}
    return verdicts


# --------------------------------------------------------------------------- #
# Plain English
# --------------------------------------------------------------------------- #

_PLAIN_RULES: tuple[tuple[str, str], ...] = (
    (r"\bshall be deemed to be\b", "counts as"),
    (r"\bis deemed to be\b", "counts as"),
    (r"\bare deemed to be\b", "count as"),
    (r"\bshall be deemed to\b", "is considered to"),
    (r"\bis deemed to\b", "is considered to"),
    (r"\bare deemed to\b", "are considered to"),
    (r"\bit shall be permissible to\b", "it is allowed to"),
    # A permission stays a permission: "shall be permitted" means "is permitted".
    (r"\bshall be permitted\b", "is permitted"),
    (r"\bshall be allowed\b", "is allowed"),
    (r"\bshall not\b", "must not"),
    (r"\bshall\b", "must"),
    (r"\bif not otherwise specified\b", "unless stated otherwise"),
    (r"\bunless otherwise specified\b", "unless stated otherwise"),
    (r"\bwith the exception of\b", "except"),
    (r"\bin the case where\b", "where"),
    (r"\bin the case of\b", "for"),
    (r"\bin respect of\b", "for"),
    (r"\bwith regard to\b", "for"),
    (r"\bin accordance with\b", "as set out in"),
    (r"\bpursuant to\b", "under"),
    (r"\bprovided that\b", "as long as"),
    (r"\bon condition that\b", "if"),
    (r"\bin the event that\b", "if"),
    (r"\bin the event of\b", "in case of"),
    (r"\bnotwithstanding\b", "despite"),
    (r"\bprior to\b", "before"),
    (r"\bsubsequent to\b", "after"),
    (r"\bby means of\b", "using"),
    (r"\bin order to\b", "to"),
    (r"\bhereinafter referred to as\b", "called"),
    (r"\bhereinafter\b", ""),
    (r"\bherein\b", "here"),
    (r"\bthereof\b", "of it"),
    (r"\btherein\b", "in it"),
    (r"\bwhereby\b", "by which"),
    (r"\bthe said\b", "the"),
    (r"\baforementioned\b", "mentioned above"),
    (r"\baforesaid\b", "mentioned above"),
    (r"\binter alia\b", "among other things"),
    (r"\bmutatis mutandis\b", "with the necessary changes"),
    (r"\bin so far as\b", "as far as"),
    (r"\binsofar as\b", "as far as"),
    (r"\bsave where\b", "except where"),
    (r"\bi\.e\.", "that is"),
    (r"\be\.g\.", "for example"),
    (r"\bPar\.\s*", "paragraph "),
    (r"\bpara\.\s*", "paragraph "),
)
_COMPILED_RULES = tuple((re.compile(p, re.IGNORECASE), r) for p, r in _PLAIN_RULES)

_LEADING_LABEL = re.compile(r"^\s*(?:(?:Annex|Appendix|Part)\s+\w+\s*[,.:]?\s*)*\d+(?:\.\d+)*\.?\s+")
_POWER_OF_TEN = re.compile(r"(\d+(?:[.,]\d+)?)\s*[∙·×x*]\s*10\s*\^\s*(-?\d+)")


def _expand_power(match: re.Match) -> str:
    mantissa, exponent = match.group(1), int(match.group(2))
    try:
        value = float(mantissa.replace(",", ".")) * (10 ** exponent)
    except ValueError:
        return match.group(0)
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if "," not in mantissa else text.replace(".", ",")


def _match_case(original: str, replacement: str) -> str:
    if original[:1].isupper() and replacement:
        return replacement[0].upper() + replacement[1:]
    return replacement


def plain_english(text: str) -> str:
    """Legal drafting to plain English. Words change; values and terms do not."""
    out = _LEADING_LABEL.sub("", text.strip(), count=1)
    out = _POWER_OF_TEN.sub(_expand_power, out)
    for pattern, replacement in _COMPILED_RULES:
        out = pattern.sub(lambda m, r=replacement: _match_case(m.group(0), r), out)
    out = re.sub(r"\s*\n\s*", " ", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    return out[:1].upper() + out[1:] if out else out


# --------------------------------------------------------------------------- #
# Requirements
# --------------------------------------------------------------------------- #

_OBLIGATION = re.compile(r"\b(shall|must)\b", re.IGNORECASE)
_FOOTNOTE = re.compile(r"^\s*(\^?\d+/|\^\d+|\(\d+\)\s|\*+\s)")
_PROCEDURAL = re.compile(
    r"\b(test|tests|testing|method|methods|procedure|procedures|measurement|measuring|"
    r"communication|conformity of production|sampling|verification|calibration|apparatus|"
    r"set-up|setup|examples? of|arrangements? of (the )?approval|model of|information document)\b",
    re.IGNORECASE,
)
"""Annex material about how a test is run or a form is filled in. Measured on the
worked example: skipping it in annexes raises page precision from 0.22 to 0.28
while page recall stays at 0.87; skipping every annex costs far more recall."""
_TEST_DESCRIPTION = re.compile(r"^\s*(this|the following) test is intended\b", re.IGNORECASE)
_NO_REQUIREMENT = re.compile(r"\bno (additional )?requirements?\b", re.IGNORECASE)
_VALUE = re.compile(
    r"(?P<cmp>not less than|not more than|not exceed(?:ing)?|at least|at most|minimum|maximum|"
    r"≥|≤|>|<)?\s*(?P<val>\d+(?:[.,]\d+)?)\s*(?P<unit>cd|lx|mm|cm\^?2|cm|m|%|°|V|W|K|ms|s|Hz|nm|kg|N|h)\b"
)
_COMPARATORS = {
    "not less than": ">=", "at least": ">=", "minimum": "min", "≥": ">=", ">": ">",
    "not more than": "<=", "at most": "<=", "maximum": "max", "≤": "<=", "<": "<",
    "not exceed": "<=", "not exceeding": "<=",
}

_GENERIC_CONTEXT = frozenset(
    {"general", "general requirements", "general specifications", "specifications",
     "requirements", "definitions", "general provisions", "scope"}
)


def _values(text: str) -> tuple[NumericValue, ...]:
    found = []
    for m in _VALUE.finditer(text):
        comparator = (m.group("cmp") or "").lower() or None
        found.append(
            NumericValue(
                quantity=None,
                value=m.group("val"),
                unit=m.group("unit"),
                comparator=_COMPARATORS.get(comparator, comparator),
            )
        )
        if len(found) >= 10:
            break
    return tuple(found)


def _clip(text: str) -> str:
    if len(text) <= MAX_TEXT_CHARS:
        return text
    cut = text[:MAX_TEXT_CHARS]
    stop = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: stop + 1] if stop > MAX_TEXT_CHARS // 2 else cut).rstrip() + " …"


def _in_annex(item: PlanItem, passage: SourcePassage) -> bool:
    label = passage.paragraph_label or item.section.paragraph_label or ""
    return label.startswith(("Annex", "Appendix")) or (passage.namespace or "main") != "main"


def _context(item: PlanItem, passage: SourcePassage) -> Optional[str]:
    """The section heading, when the paragraph sits under a device-specific one."""
    heading = (item.section.heading or "").strip().rstrip(":.")
    if not heading or heading.lower() in _GENERIC_CONTEXT:
        return None
    if passage.paragraph_label and passage.paragraph_label == item.section.paragraph_label:
        return None  # the paragraph is the heading's own text
    if heading.lower() in passage.text.lower():
        return None
    return heading


def has_obligation_wording(ctx: RunContext, item: PlanItem) -> bool:
    """Whether any primary passage of the section states an obligation at all."""
    try:
        passages = ctx.corpus.get_passages(list(item.section.primary_passage_ids))
    except Exception:
        return True  # unreadable here is not proof of emptiness; let extraction decide
    return any(not p.is_table and _OBLIGATION.search(p.text) for p in passages) or any(
        p.is_table for p in passages
    )


def economy_extract_section(ctx: RunContext, item: PlanItem) -> SectionResult:
    """One plain-English requirement per obligation paragraph of the section."""
    try:
        passages = ctx.corpus.get_passages(list(item.section.primary_passage_ids))
    except Exception as exc:  # a corpus failure is a failed section, not a crash
        return SectionResult(
            drafts=[], inspected_passage_ids=[], resolutions=[], notes=[],
            failed=True, failure_reason=f"corpus read failed: {exc}",
        )

    drafts: list[DraftRequirement] = []
    for passage in passages:
        if passage.is_table or not _OBLIGATION.search(passage.text):
            continue
        body = _LEADING_LABEL.sub("", passage.text.strip(), count=1)
        if _NO_REQUIREMENT.search(body) and len(body) < 120:
            continue  # "No requirements." is an exclusion, not an obligation
        if _FOOTNOTE.match(body) or _TEST_DESCRIPTION.match(body):
            continue  # a footnote or a test method's description, not a requirement on the part
        if _in_annex(item, passage) and (
            _PROCEDURAL.search(" ".join((item.section.heading or "", *passage.heading_ancestry)))
            or _PROCEDURAL.search(body[:160])
        ):
            continue  # test method or paperwork in an annex, not a requirement on the part
        text = plain_english(passage.text)
        context = _context(item, passage)
        if context:
            text = f"For {context[0].lower() + context[1:] if context[1:2].islower() else context}: {text}"
        text = _clip(text)
        drafts.append(
            DraftRequirement(
                draft_id=draft_id_for(item.section_id, passage.passage_id, text),
                text=text,
                supporting_passage_ids=(passage.passage_id,),
                primary_passage_id=passage.passage_id,
                obligation_kind=ObligationKind.MANDATORY,
                applicability_reason=f"Paragraph of {item.document.regulation} "
                f"{item.section.paragraph_label or item.section.heading} kept as relevant to this part.",
                values=_values(passage.text),
                document_id=item.document.document_id,
                section_id=item.section_id,
                prompt_version="economy-v1",
                notes=ECONOMY_NOTE,
            )
        )

    return SectionResult(
        drafts=drafts,
        inspected_passage_ids=[p.passage_id for p in passages],
        resolutions=[],
        notes=[ECONOMY_NOTE] if drafts else [],
    )

