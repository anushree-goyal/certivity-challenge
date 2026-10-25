"""Regulation corpus: the participant package's index plus the base PDFs, split into numbered paragraphs.

No model is involved here. Each base PDF is read once with PyMuPDF and cached as JSON next to the other
caches, so later runs only load the cache.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import pymupdf

from certivity.config import settings

PARSER_VERSION = 9

# "UN R150, version 1.2.1 | page 7 of 54" - the running header on every page.
HEADER = re.compile(r"^UN .*\|\s*page \d+ of \d+\s*$")
# "6.9.1." or "6.9.1. Text..." - UN regulations end every paragraph number with a dot.
LABEL = re.compile(r"^((?:\d{1,2}\.){1,7})(?:\s+(.*))?$")
# "Annex 9 Method(s) of testing..." starts an annex; its paragraphs restart at "1.". Body text that wraps onto
# "Annex 5 of this Regulation" is told apart by the capitalised title (or nothing / "- Appendix") after the number.
ANNEX = re.compile(r"^Annex\s+(\d{1,2})([A-Z]?)(?:$|\s+[-–(]?\s*[A-Z])")
# "Part 1 - Resistance to heat" inside an annex: cited as "Annex 6 Part 1 3".
PART = re.compile(r"^Part\s+(\d{1,2})\s*[-–]")
# Obligations, prohibitions and permissions all count as requirements, and test procedures speak in the imperative.
OBLIGATION = re.compile(
    r"\b(shall|must|may|required|permitted|prohibited|allowed|need not|not exceed|at least|at most|comply|complies)\b"
    r"|^(apply|use|measure|test|check|ensure|place|mount)\b",
    re.I,
)
# A wrapped line that starts with a cross-reference ("...prescribed in paragraph / 3.2.3.2. shall be...").
REFERENCE_END = re.compile(r"\b(paragraphs?|para\.|annex(es)?|and|or|to|of|in|see|under|with)$", re.I)


@dataclass
class Paragraph:
    regulation: str
    version: str
    label: str  # "6.9.1" or "Annex 9 3.1"
    page: int  # physical PDF page, 1-based (what the submission's source.page means)
    text: str
    heading: str  # section path, e.g. "6. Individual specifications > 6.20. Cornering lamp"
    sections: list[str] = field(default_factory=list)  # ["6", "6.20"] or ["Annex 9"]: what scoping can drop

    @property
    def id(self) -> str:
        return f"{self.regulation}|{self.label}"

    @property
    def is_obligation(self) -> bool:
        return bool(OBLIGATION.search(self.text))


@dataclass
class Document:
    regulation: str
    title: str
    version: str
    pdf: Path | None
    sha256: str | None

    @property
    def document_id(self) -> str:
        return f"{self.regulation.replace(' ', '_')}@{self.version}"


def load_index() -> list[dict]:
    return json.loads((settings.package_dir / "regulations" / "index.json").read_text())


def documents() -> dict[str, Document]:
    """The base version of every regulation in the index, keyed by regulation name."""
    out = {}
    for entry in load_index():
        base = next((v for v in entry["versions"] if v["role"] == "base"), None)
        if not base:
            continue
        f = base["files"][0]
        pdf = settings.package_dir / "regulations" / "pdf" / entry["regulation"].replace(" ", "_") / "base" / f["name"]
        out[entry["regulation"]] = Document(
            entry["regulation"], entry.get("title", ""), base["version_label"], pdf if pdf.is_file() else None, f.get("sha256"),
        )
    return out


def document_by_id(document_id: str) -> Document | None:
    return next((d for d in documents().values() if d.document_id == document_id), None)


def pdf_verified(doc: Document) -> bool:
    return bool(doc.pdf and doc.sha256 and hashlib.sha256(doc.pdf.read_bytes()).hexdigest() == doc.sha256)


def parse_pdf(regulation: str, version: str, pdf: Path) -> list[Paragraph]:
    # Each paragraph remembers its annex title and its section ("6.") and sub-section ("6.20.") paragraphs; the
    # heading text is built at the end, because a heading's own text may continue on the next line.
    paragraphs: list[tuple[Paragraph, str | None, str, Paragraph | None, Paragraph | None]] = []
    annex: str | None = None
    annex_title = ""
    part: str | None = None
    section = subsection = current = None
    with pymupdf.open(pdf) as doc:
        for index, page in enumerate(doc):
            for raw in page.get_text().splitlines():
                line = raw.strip()
                if not line or HEADER.match(line):
                    continue
                if (m := ANNEX.match(line)) and _next_annex(annex, m.group(1)):
                    annex, annex_title, part = m.group(1) + m.group(2), line[:100], None
                    section = subsection = current = None
                    continue
                if annex and (m := PART.match(line)):
                    part, section, subsection, current = m.group(1), None, None, None
                    continue
                m = LABEL.match(line)
                if m and current is not None and REFERENCE_END.search(current.text):
                    m = None  # a cross-reference that wrapped onto a new line, not a new paragraph
                if m:
                    number = m.group(1).rstrip(".")
                    label = f"Annex {annex}{f' Part {part}' if part else ''} {number}" if annex else number
                    current = Paragraph(regulation, version, label, index + 1, (m.group(2) or "").strip(), "")
                    depth = number.count(".")
                    if depth == 0:
                        section, subsection = current, None
                    elif depth == 1:
                        subsection = current
                    paragraphs.append((current, annex, annex_title, section, subsection))
                elif current is not None:
                    current.text = f"{current.text}\n{line}".strip()  # line breaks keep lists and table rows apart

    def title(p: Paragraph) -> str:
        return f"{p.label.split()[-1]}. {' '.join(p.text.split())[:110]}"

    for p, annex, annex_title, section, subsection in paragraphs:
        p.heading = " > ".join([annex_title] * bool(annex_title) + [title(s) for s in (section, subsection) if s])
        p.sections = [f"Annex {annex}"] if annex else [s.label for s in (section, subsection) if s]
    return [p for p, *_ in paragraphs if p.text]


def _next_annex(current: str | None, number: str) -> bool:
    """Annexes run 1, 2, 2A, 3...; anything else is a reference in the text, not a heading."""
    if current is None:
        return number == "1"
    return int(number) >= int(re.match(r"\d+", current).group())


def _cache_path(doc: Document) -> Path:
    return settings.cache_dir / "paragraphs" / f"{doc.document_id}.v{PARSER_VERSION}.json"


@lru_cache(maxsize=None)
def paragraphs(regulation: str) -> tuple[Paragraph, ...]:
    """Every numbered paragraph of the regulation's base PDF, in reading order. Empty if the PDF is missing."""
    doc = documents().get(regulation)
    if not doc or not doc.pdf:
        return ()
    cache = _cache_path(doc)
    if cache.is_file():
        return tuple(Paragraph(**p) for p in json.loads(cache.read_text()))
    parsed = parse_pdf(doc.regulation, doc.version, doc.pdf)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([asdict(p) for p in parsed]))
    return tuple(parsed)


def scope_text(regulation: str, limit: int = 350) -> str:
    """The start of the regulation's section 1 (its scope): what the regulation applies to."""
    section = [p for p in paragraphs(regulation) if not p.label.startswith("Annex") and p.sections[:1] == ["1"]]
    return " ".join(" ".join(p.text.split()) for p in section)[:limit]


def outline(paragraphs: list[Paragraph]) -> list[tuple[str, str]]:
    """(section key, title) for every section the given paragraphs fall in, in reading order: the table of
    contents a reader skims to see which parts of a regulation concern a product."""
    titles: dict[str, str] = {}
    for p in paragraphs:
        parts = p.heading.split(" > ")
        if p.label.startswith("Annex"):
            titles.setdefault(p.sections[0], parts[0])
        else:
            for key, text in zip(p.sections, parts):
                titles.setdefault(key, text)
    return list(titles.items())


def page_count(doc: Document) -> int | None:
    if not doc.pdf:
        return None
    with pymupdf.open(doc.pdf) as d:
        return d.page_count


def render_page(doc: Document, pdf_page_index: int) -> bytes:
    cache = settings.cache_dir / "pages" / doc.document_id / f"{pdf_page_index}.png"
    if not cache.is_file():
        with pymupdf.open(doc.pdf) as d:
            png = d[pdf_page_index].get_pixmap(dpi=110).tobytes("png")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(png)
    return cache.read_bytes()
