"""Text-level rules for UNECE regulation structure: paragraph labels, namespaces, definitions, references.

Pure functions only (no PDF or SQL), so they are easy to unit test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- paragraph labels -------------------------------------------------------------------------

# "6.10.4.2. In height", "1. Scope", "A.1.4.1.2. If the", "2.1. General". A trailing dot is required:
# continuation lines such as "60 ± 2 ..." or "10 m" must not become labels.
LABEL_LINE = re.compile(r"^(?P<label>(?:[A-Z]\.)?\d{1,3}(?:\.\d{1,3}){0,9}\.)(?:\s+(?P<rest>\S.*))?$")

# "5.7 Grouped, combined ..." - some headings drop the trailing dot. Only multi-level, only before a capital,
# and only accepted when it is the exact next label (see judge_label strict=True).
LABEL_LINE_NODOT = re.compile(r"^(?P<label>\d{1,3}(?:\.\d{1,3}){1,9})\s+(?P<rest>[A-Z].*)$")

# A line that ends like this is mid-sentence, so a "label" at the start of the next line is usually a
# cross-reference ("... paragraph\n5.8.1. above"). Strong endings block anything but an in-sequence
# label that starts a capitalised sentence; weak endings (commas, conjunctions) block only out-of-sequence ones.
_CONTINUES_STRONG = re.compile(r"(?:\bparagraphs?|\bparas?\.|\bAnnex|\bAppendix|\bNo\.|\bNos\.|\bto|\band)$", re.IGNORECASE)
_CONTINUES_WEAK = re.compile(r"(?:,|\bor|\bof|\bin|\bwith|\bsee|\bunder|\bthan|\bfrom|\bby|\bthe|\ba|\ban)$", re.IGNORECASE)


def parse_label(label: str) -> tuple:
    """'A.1.4.' -> ('A', 1, 4); '6.10.' -> (6, 10)."""
    parts = label.rstrip(".").split(".")
    out: list = []
    for part in parts:
        out.append(int(part) if part.isdigit() else part)
    return tuple(out)


def label_numbers(key: tuple) -> tuple[int, ...]:
    return tuple(p for p in key if isinstance(p, int))


@dataclass(frozen=True)
class LabelVerdict:
    accept: bool
    flags: tuple[str, ...] = ()


def judge_label(candidate: tuple, previous: tuple | None, previous_line: str, rest: str | None) -> LabelVerdict:
    """Decide whether a label-looking line start is a new paragraph, given the last accepted label in the namespace."""
    prev_text = previous_line.rstrip()
    in_sequence = previous is not None and _is_successor(candidate, previous)
    starts_upper = bool(rest) and (rest[0].isupper() or rest[0] in "(\"“'[")
    if prev_text and _CONTINUES_STRONG.search(prev_text) and not (in_sequence and starts_upper):
        return LabelVerdict(False)
    if prev_text and _CONTINUES_WEAK.search(prev_text) and not in_sequence:
        return LabelVerdict(False)
    if previous is None:
        return LabelVerdict(True, () if label_numbers(candidate)[:1] in ((1,), ()) else ("label_unexpected_start",))
    if in_sequence:
        return LabelVerdict(True)
    gap = _is_successor(candidate, previous, max_step=9)
    if gap and starts_upper:
        return LabelVerdict(True, ("label_gap",))
    if candidate == previous and starts_upper:
        return LabelVerdict(True, ("duplicate_label",))
    if starts_upper:
        return LabelVerdict(True, ("label_out_of_sequence",))
    return LabelVerdict(False)


def _is_successor(candidate: tuple, previous: tuple, max_step: int = 1) -> bool:
    if len(candidate) and len(previous) and type(candidate[0]) is not type(previous[0]):
        return False
    # child: 6.10 -> 6.10.1 (or a chain of .1s)
    if len(candidate) > len(previous) and candidate[: len(previous)] == previous:
        tail = candidate[len(previous):]
        return all(isinstance(t, int) and 1 <= t <= max_step for t in tail[:1]) and all(t == 1 for t in tail[1:])
    # sibling of the previous label or of one of its ancestors: 6.10.4.2 -> 6.10.5 / 6.11 / 7
    for depth in range(len(previous), 0, -1):
        if len(candidate) != depth:
            continue
        head, last = previous[: depth - 1], previous[depth - 1]
        if candidate[: depth - 1] == head and isinstance(last, int) and isinstance(candidate[-1], int):
            return 1 <= candidate[-1] - last <= max_step
    return False


# --- namespaces ---------------------------------------------------------------------------------

_DASH = r"\s*[-–—:]\s*"
_SEP = rf"(?:{_DASH}|\s+)"
ANNEX_HEADING = re.compile(
    rf"^Annex\s+(?P<annex>\d+[A-Z]?|[IVX]+)\b(?:{_SEP}Part\s+(?P<part>\d+[A-Z]?|[IVX]+|[A-Z])\b)?"
    rf"(?:{_SEP}(?P<appx_kw>Appendix)(?:\s+(?P<appendix>\d+[A-Z]?|[IVX]+|[A-Z])\b)?)?(?P<rest>.*)$",
    re.IGNORECASE,
)
# "Annex 1 3 RESISTANCE TO IMPACT" is "Annex 13" with the digits split by the renderer (R3).
ANNEX_SPLIT_DIGITS = re.compile(r"^Annex\s+(?P<a>\d)\s(?P<b>\d)\s+(?P<rest>[A-Z(].*)$")
# GTR layout: a non-normative statement of rationale precedes the text of the regulation.
GTR_RATIONALE = re.compile(r"^[IVX]+\.\s+Statement of technical rationale", re.IGNORECASE)
GTR_TEXT = re.compile(r"^[IVX]+\.\s+Text of the (?:global technical )?Regulation\b", re.IGNORECASE)
RATIONALE = "Part I"
INTRODUCTION = "Introduction"
INTRODUCTION_HEADING = re.compile(r"^Introduction\b", re.IGNORECASE)
SCOPE_HEADING = re.compile(r"^Scope\b", re.IGNORECASE)
# "1 Scope": a major heading whose single-level label lost its dot (R148)
MAJOR_LABEL_NODOT = re.compile(r"^(?P<label>\d{1,2})\s+(?P<rest>[A-Z].*)$")
APPENDIX_HEADING = re.compile(r"^Appendix\s+(?P<appendix>\d+[A-Z]?|[IVX]+)\b(?P<rest>.*)$", re.IGNORECASE)
PART_HEADING = re.compile(rf"^Part\s+(?P<part>\d+[A-Z]?|[IVX]+|[A-Z])\b(?:{_DASH}|\s+)?(?P<rest>.*)$", re.IGNORECASE)
CAPTION = re.compile(r"^(?P<kind>Table|Figure)\s+(?P<name>[A-Z]{0,3}\d+[A-Za-z]?(?:[-.]\d+[A-Za-z]?)*)\b", re.IGNORECASE)

MAIN = "main"


def namespace_label(annex: str | None = None, part: str | None = None, appendix: str | None = None) -> str:
    bits = []
    if annex:
        bits.append(f"Annex {annex}")
    if part:
        bits.append(f"Part {part}")
    if appendix:
        bits.append(f"Appendix {appendix}".rstrip())
    return " ".join(bits) or MAIN


def citation_label(namespace: str, label: str | None) -> str | None:
    """The paragraph string other components cite: '6.10.4.2' or 'Annex 6 Part 6 3'."""
    bare = label.rstrip(".") if label else None
    if namespace == MAIN:
        return bare
    return f"{namespace} {bare}" if bare else namespace


def namespace_parts(namespace: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in re.findall(r"(Annex|Part|Appendix)\s+(\S+)", namespace):
        out[key.lower()] = value
    return out


# --- definitions --------------------------------------------------------------------------------

_QUOTE = "\"“”„'‘’"
DEFINITION = re.compile(
    rf"^[{_QUOTE}](?P<term>[^{_QUOTE}]{{2,90}})[{_QUOTE}]\s*(?:\([^)]{{0,40}}\)\s*)?,?\s*(?:of\s+(?:a|an|the)\s+[\w-]+\s+)?(?:on\s+either\s+side[^,]*,\s*)?"
    r"(?:means|mean|is|are|shall\s+mean|refers?\s+to|denotes|is\s+defined)\b",
    re.IGNORECASE,
)


def definition_terms(text: str) -> list[str]:
    match = DEFINITION.match(text.strip())
    if not match:
        return []
    term = " ".join(match.group("term").split())
    terms = [term]
    # '"Lamp" or "light"' style alternatives are rare; '"Dipped-beam headlamp" (...)' is handled by the regex.
    return terms


# --- references ---------------------------------------------------------------------------------

_NUM = r"\d{1,3}(?:\.\d{1,3})*\.?"
_PARA_LIST = rf"{_NUM}(?:\s*(?:,|and|or|to|-|–)\s*{_NUM})*"
_ANNEX_REF = r"Annex\s+(?P<annex>\d+[A-Z]?)"
_APPX_OF_ANNEX = r"(?:Part\s+(?P<part>\d+[A-Z]?)\s+(?:of|to)\s+)?(?:Appendix\s+(?P<appendix>\d+[A-Z]?)\s+(?:of|to)\s+)?"
_REG = r"(?:UN\s+)?Regulations?\s+No(?:s)?\.?\s*(?P<reg>\d{1,3}[A-Z]?)"

REFERENCE_PATTERNS = [
    # "paragraph 5.1.7. of Annex 4", "paragraphs 2.2.1.1. to 2.2.1.4.", "paragraph 6.2 of UN Regulation No. 10"
    re.compile(
        rf"\bparagraphs?\s+(?P<paras>{_PARA_LIST})(?:\s*(?:above|below))?"
        rf"(?:\s*(?:of|in|to)\s+(?:(?P<this_annex>this\s+annex|this\s+appendix)|{_APPX_OF_ANNEX}{_ANNEX_REF}|(?P<this_reg>this\s+Regulation)|{_REG}))?",
        re.IGNORECASE,
    ),
    # "Annex 4, paragraph 2.1", "Appendix 1 to Annex 5", "Part 2 of Annex 6", "Annex 3, Part 7", bare "Annex 5"
    re.compile(
        rf"\b{_APPX_OF_ANNEX}{_ANNEX_REF}(?:\s*,?\s*Part\s+(?P<part2>\d+[A-Z]?))?(?:\s*,?\s*Appendix\s+(?P<appendix2>\d+[A-Z]?))?"
        rf"(?:\s*,?\s*paragraphs?\s+(?P<paras>{_PARA_LIST}))?(?:\s+(?:of|to)\s+{_REG})?",
        re.IGNORECASE,
    ),
    # "UN Regulation No. 48", "Regulations Nos. 37, 99 and 128"
    re.compile(rf"\b(?:UN\s+)?Regulations?\s+Nos?\.?\s*(?P<regs>\d{{1,3}}[A-Z]?(?:\s*(?:,|and|or)\s*\d{{1,3}}[A-Z]?)*)", re.IGNORECASE),
]


@dataclass(frozen=True)
class ReferenceCandidate:
    raw: str
    start: int
    end: int
    target_regulation_number: str | None  # "48", not yet mapped to the index
    target_annex: str | None  # namespace-style hint: "Annex 4", "Annex 6 Part 2", "Annex 5 Appendix 1", "this annex"
    target_paragraph: str | None  # "5.1.7", or a range "2.2.1.1-2.2.1.4"
    explicit_this_regulation: bool = False


def _split_paras(paras: str) -> list[str]:
    tokens = re.findall(rf"{_NUM}|to|-|–", paras)
    out: list[str] = []
    pending_range = False
    for tok in tokens:
        if tok in ("to", "-", "–"):
            pending_range = True
            continue
        num = tok.rstrip(".")
        if pending_range and out:
            out[-1] = f"{out[-1]}-{num}"
            pending_range = False
        else:
            out.append(num)
    return out


def find_references(text: str) -> list[ReferenceCandidate]:
    flat = " ".join(text.split())
    found: list[ReferenceCandidate] = []
    taken: list[tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(a < y and x < b for x, y in taken)

    for index, pattern in enumerate(REFERENCE_PATTERNS):
        for match in pattern.finditer(flat):
            if overlaps(match.start(), match.end()):
                continue
            groups = match.groupdict()
            if index == 2:
                for reg in re.findall(r"\d{1,3}[A-Z]?", groups["regs"]):
                    found.append(ReferenceCandidate(match.group(0), match.start(), match.end(), reg, None, None))
                taken.append((match.start(), match.end()))
                continue
            annex = None
            if groups.get("this_annex"):
                annex = "this annex"
            elif groups.get("annex"):
                annex = namespace_label(groups["annex"], groups.get("part") or groups.get("part2"), groups.get("appendix") or groups.get("appendix2"))
            paras = _split_paras(groups["paras"]) if groups.get("paras") else [None]
            for para in paras:
                found.append(
                    ReferenceCandidate(
                        raw=match.group(0).strip(),
                        start=match.start(),
                        end=match.end(),
                        target_regulation_number=groups.get("reg"),
                        target_annex=annex,
                        target_paragraph=para,
                        explicit_this_regulation=bool(groups.get("this_reg")),
                    )
                )
            taken.append((match.start(), match.end()))
    found.sort(key=lambda c: c.start)
    return found


_REG_HINT = re.compile(r"^(?:UN\s*)?(?:R|Regulation\s+No\.?\s*|Reg\.?\s*)?(?P<num>\d{1,3}[A-Z]?)$", re.IGNORECASE)


def regulation_number_from_hint(hint: str) -> str | None:
    """'UN R48' / 'R48' / '48' / 'Regulation No. 48' -> '48'. GTRs and other names return None."""
    match = _REG_HINT.match(hint.strip())
    return match.group("num").upper() if match else None
