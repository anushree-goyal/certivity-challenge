"""Parse one Certivity-rendered regulation PDF into pages, passages, sections and diagnostics.

The supplied PDFs share one layout: a footer "UN R48, version 9.3.0 | page 50 of 122" on every page,
bold headings for clauses and annexes, paragraph labels at the start of a line, tables drawn as
separate cells. Thresholds are relative to each document's own body font and left margin, because
these differ between documents (R49 uses 8.6 pt body text, R48 10.5 pt).

Output is plain dicts/dataclasses so it can be cached as JSON and imported into SQLite.
"""

from __future__ import annotations

import collections
import re
from dataclasses import asdict, dataclass, field

import pymupdf

from . import structure as st

PARSER_VERSION = "corpus-parse-13"

FOOTER = re.compile(r"^(?P<reg>.+?),\s*version\s+(?P<ver>\S+)\s*\|\s*page\s+(?P<page>\d+)\s+of\s+(?P<total>\d+)$")
BANNER = re.compile(r"^(Consolidated text of this version, rendered by Certivity|Role in the hackathon:|the bottom of every page)", re.IGNORECASE)
_LIST_START = re.compile(r"^(\([a-z0-9ivx]{1,4}\)|[a-z]\)|[•▪◦\-–]\s|\d+\)\s)")


@dataclass
class Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    page: int


@dataclass
class PageInfo:
    pdf_page_index: int
    printed_page: int | None
    printed_label_verified: bool
    has_images: bool
    has_tables: bool
    drawing_count: int
    text_chars: int


@dataclass
class ParsedPassage:
    key: str  # unique within the document
    namespace: str
    label: str | None  # raw label as printed, e.g. "6.10.4.2."
    kind: str  # paragraph | heading | intro | table | footnote | front_matter
    text: str
    pdf_pages: list[int] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    order: int = 0
    table_rows: list[list[str]] | None = None
    title: str | None = None  # short title line of a labelled paragraph ("Presence", "Rear position lamp")


@dataclass
class Diagnostic:
    severity: str  # info | warning | error
    code: str
    message: str
    pdf_page_index: int | None = None


@dataclass
class ParsedDocument:
    parser_version: str
    page_count: int
    footer_regulation: str | None
    footer_version: str | None
    body_size: float
    left_margin: float
    pages: list[PageInfo]
    passages: list[ParsedPassage]
    namespace_headings: dict[str, str]
    diagnostics: list[Diagnostic]

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "ParsedDocument":
        return cls(
            parser_version=data["parser_version"],
            page_count=data["page_count"],
            footer_regulation=data["footer_regulation"],
            footer_version=data["footer_version"],
            body_size=data["body_size"],
            left_margin=data["left_margin"],
            pages=[PageInfo(**p) for p in data["pages"]],
            passages=[ParsedPassage(**p) for p in data["passages"]],
            namespace_headings=data["namespace_headings"],
            diagnostics=[Diagnostic(**d) for d in data["diagnostics"]],
        )


# --- low-level extraction ---------------------------------------------------------------------


SUPERSCRIPT_FLAG = 1  # PyMuPDF span flag bit 0


def _is_superscript(span: dict, main: dict) -> bool:
    """Flagged superscript, or (for PDFs that do not set the flag) smaller and raised above the main baseline."""
    if span is main:
        return False
    if span["flags"] & SUPERSCRIPT_FLAG:
        return True
    return span["size"] < main["size"] * 0.85 and span["origin"][1] < main["origin"][1] - 1.0


def spans_text(spans: list[dict]) -> str:
    """Join a line's spans, keeping superscripts explicit as ``^``: ``4∙10`` + ``0`` -> ``4∙10^0``,
    ``2.5∙10`` + ``-1`` + ``cd`` -> ``2.5∙10^-1 cd``, ``100 cm`` + ``2`` -> ``100 cm^2``. Flattened, these read as
    ``4∙100`` / ``100 cm2`` - a wrong limit that looks perfectly citable (R148 photometry)."""
    spans = [s for s in spans if s["text"]]
    if not spans:
        return ""
    main = max(spans, key=lambda s: len(s["text"].strip()))
    out = ""
    previous_sup = False
    for span in spans:
        text = span["text"]
        if _is_superscript(span, main) and text.strip():
            out += "^" + text.strip()
            previous_sup = True
            continue
        if previous_sup and text[:1].isalnum():
            out += " "  # "10^-1cd" -> "10^-1 cd": the renderer drew a gap, not a space character
        out += text
        previous_sup = False
    return out


def _page_lines(page: pymupdf.Page, index: int) -> list[Line]:
    lines: list[Line] = []
    for block in page.get_text("dict")["blocks"]:
        for raw in block.get("lines", []):
            spans = [s for s in raw["spans"] if s["text"]]
            text = spans_text(spans)
            if not text.strip():
                continue
            main = max(spans, key=lambda s: len(s["text"].strip()))
            x0, y0, x1, y1 = raw["bbox"]
            lines.append(Line(" ".join(text.split()), x0, y0, x1, y1, main["size"], bool(main["flags"] & 16), index))
    lines.sort(key=lambda l: (round(l.y0 / 2), l.x0))
    return lines


def _layout_stats(all_lines: list[Line]) -> tuple[float, float]:
    sizes: collections.Counter = collections.Counter()
    margins: collections.Counter = collections.Counter()
    for line in all_lines:
        sizes[round(line.size, 1)] += len(line.text)
        margins[round(line.x0)] += len(line.text)
    return sizes.most_common(1)[0][0], float(margins.most_common(1)[0][0])


def _cell_text(page: pymupdf.Page, bbox) -> str:
    """Cell text read with a clip (Table.extract() drops decimal points), superscripts kept as ``^``."""
    rect = pymupdf.Rect(bbox)
    parts = []
    for block in page.get_text("dict", clip=rect)["blocks"]:
        for line in block.get("lines", []):
            text = spans_text(line["spans"])
            if text.strip():
                parts.append(text)
    return " ".join(" ".join(parts).split())


def _tables(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, list[list[str]]]]:
    """Table geometry from PyMuPDF; cell text re-read with a clip, because Table.extract() drops characters such as decimal points."""
    out = []
    try:
        found = page.find_tables()
    except Exception:  # noqa: BLE001 - table finding is best effort; reported as a diagnostic by the caller
        return out
    for table in found.tables:
        rows: list[list[str]] = []
        for row in table.rows:
            cells = [_cell_text(page, c) if c else "" for c in row.cells]
            if any(cells):
                rows.append(cells)
        if rows:
            out.append((pymupdf.Rect(table.bbox), rows))
    return out


def _inside(line: Line, rect: pymupdf.Rect) -> bool:
    cx, cy = (line.x0 + line.x1) / 2, (line.y0 + line.y1) / 2
    return rect.x0 - 1 <= cx <= rect.x1 + 1 and rect.y0 - 1 <= cy <= rect.y1 + 1


def _join(lines: list[str]) -> str:
    out = ""
    for text in lines:
        if not out:
            out = text
        elif out.endswith("-") and (text[:1].islower() or (text[:1].isdigit() and out[-2:-1].isalnum())):
            out += text
        elif _LIST_START.match(text) or out.endswith((":", ";")) or st.CAPTION.match(text):
            out += "\n" + text
        else:
            out += " " + text
    return out


# --- document parser --------------------------------------------------------------------------


class _Builder:
    def __init__(self) -> None:
        self.passages: list[ParsedPassage] = []
        self.current: ParsedPassage | None = None
        self.current_lines: list[str] = []
        self.namespace = st.MAIN
        self.annex: str | None = None
        self.last_label: dict[str, tuple] = {}
        self.keys: collections.Counter = collections.Counter()
        self.namespace_headings: dict[str, str] = {}
        self.diagnostics: list[Diagnostic] = []
        self.previous_line = ""
        self.enclosing_label: str | None = None
        self.last_caption: tuple[str, int, float] | None = None  # (caption text, page, y)
        self.last_caption_passage: ParsedPassage | None = None
        self.flow_after_table = False
        self.in_rationale = False  # inside a GTR "Statement of technical rationale" (not any namespace named "Part I")

    # passage lifecycle
    def _key(self, base: str) -> str:
        self.keys[base] += 1
        return base if self.keys[base] == 1 else f"{base}#{self.keys[base]}"

    def flush(self) -> None:
        if self.current is not None:
            lines = self.current_lines
            if self.current.label and self.current.title and len(lines) > 1:
                self.current.text = lines[0] + "\n" + _join(lines[1:])
            else:
                self.current.text = _join(lines)
            self.current.pdf_pages = sorted(set(self.current.pdf_pages))
            if self.current.text.strip():
                self.current.order = len(self.passages)
                self.passages.append(self.current)
        self.current, self.current_lines = None, []

    def start(self, kind: str, label: str | None, page: int, flags: list[str] | None = None) -> None:
        self.flush()
        if label:
            base = f"{self.namespace}|{label.rstrip('.')}"
        else:
            base = f"{self.namespace}|_{kind}"
        self.current = ParsedPassage(self._key(base), self.namespace, label, kind, "", [page], list(flags or []))

    def add_text(self, text: str, page: int, flags: tuple[str, ...] = ()) -> None:
        if self.current is None or self.current.kind in ("table", "footnote"):
            # text after a table/footnote returns to the enclosing paragraph as a continuation passage
            resumed = self.enclosing_label
            self.start("paragraph" if resumed else ("front_matter" if self.namespace == st.MAIN and not self.last_label.get(st.MAIN) else "intro"), resumed, page)
            if resumed:
                self.current.flags.append("continuation_after_table")
        self.current_lines.append(text)
        self.current.pdf_pages.append(page)
        for flag in flags:
            if flag not in self.current.flags:
                self.current.flags.append(flag)
        self.previous_line = text

    # structure events
    def set_namespace(self, namespace: str, heading: str, page: int) -> None:
        self.flush()
        self.namespace = namespace
        self.in_rationale = False
        self.enclosing_label = None
        if namespace in self.namespace_headings and namespace != st.MAIN:
            self.diagnostics.append(Diagnostic("warning", "namespace_repeated", f"{namespace} heading seen again: {heading!r}", page))
        self.namespace_headings.setdefault(namespace, heading)
        self.start("heading", None, page)
        self.current_lines.append(heading)
        self.previous_line = ""

    def label(self, label: str, rest: str | None, page: int, flags: tuple[str, ...]) -> None:
        self.start("paragraph", label, page, list(flags))
        if self.current.key != f"{self.namespace}|{label.rstrip('.')}":
            self.current.flags.append("duplicate_label")
        self.last_label[self.namespace] = st.parse_label(label)
        self.enclosing_label = label
        self.current_lines.append(f"{label} {rest}" if rest else label)
        self.previous_line = rest or ""
        if rest and len(rest) <= 80 and not rest.rstrip().endswith((".", ",", ";", ":", "-")):
            self.current.title = rest.strip()  # confirmed as a title only if more lines follow (see flush)

    def caption(self, text: str, page: int, y: float) -> None:
        """A figure/table caption is its own passage; like a footnote it does not close the open paragraph,
        so the paragraph's text continues unbroken after the figure (R148 3.3.1.2)."""
        label = self.enclosing_label
        named = st.CAPTION.match(text)
        name = named.group("name") if named else "untitled"
        passage = ParsedPassage(self._key(f"{self.namespace}|{(label or '').rstrip('.')}|caption:{name}"), self.namespace, label, "caption", text, [page], ["caption"])
        self.passages.append(passage)
        self.last_caption = (text, page, y)
        self.last_caption_passage = passage

    def table(self, rows: list[list[str]], page: int, caption: str | None) -> None:
        if caption and self.last_caption_passage is not None and self.last_caption_passage.text == caption:
            self.passages.remove(self.last_caption_passage)  # the table passage carries its own caption
            self.last_caption_passage = None
        label = self.enclosing_label
        self.start("table", label, page, ["table"] + ([] if caption else ["table_without_caption"]))
        named = st.CAPTION.match(caption) if caption else None
        self.current.key = self._key(f"{self.namespace}|{(label or '').rstrip('.')}|table:{named.group('name') if named else 'untitled'}")
        self.current.table_rows = rows
        self.current.flags.append(f"caption:{caption}" if caption else "caption:none")
        self.current.text = "\n".join([caption or "Table"] + [" | ".join(row) for row in rows])
        self.current.pdf_pages = [page]
        self.current.order = len(self.passages)
        self.passages.append(self.current)
        self.current, self.current_lines = None, []

    def finish(self) -> list[ParsedPassage]:
        self.flush()
        for p in self.passages:
            lines = getattr(p, "_lines", None)
            if lines is not None:
                p.text = _join(lines)
                del p._lines  # type: ignore[attr-defined]
        self.passages = [p for p in self.passages if p.text.strip()]
        for i, p in enumerate(self.passages):
            p.order = i
        return self.passages


def parse_pdf(path: str) -> ParsedDocument:
    doc = pymupdf.open(path)
    builder = _Builder()
    pages: list[PageInfo] = []
    footer_reg = footer_ver = None

    per_page: list[list[Line]] = []
    for index, page in enumerate(doc):
        per_page.append(_page_lines(page, index))
    body_size, margin = _layout_stats([l for lines in per_page for l in lines])
    heading_size = body_size * 1.15
    footnote_size = body_size * 0.82

    for index, page in enumerate(doc):
        lines = per_page[index]
        printed, verified = None, False
        content: list[Line] = []
        for line in lines:
            match = FOOTER.match(line.text)
            if match and line.y0 > page.rect.height * 0.85:
                printed = int(match["page"])
                verified = printed == index + 1 and int(match["total"]) == doc.page_count
                footer_reg, footer_ver = match["reg"], match["ver"]
                continue
            content.append(line)
        if printed is None:
            builder.diagnostics.append(Diagnostic("error", "footer_missing", "no printed page label found", index))
        elif not verified:
            builder.diagnostics.append(Diagnostic("error", "footer_mismatch", f"footer says page {printed}, physical page is {index + 1}", index))

        offset_lines = [l for l in content if l.x0 > margin + 4]
        tables = _tables(page) if offset_lines else []
        images = bool(page.get_images(full=False))
        try:
            drawings = len(page.get_cdrawings())
        except Exception:  # noqa: BLE001
            drawings = 0
        pages.append(PageInfo(index, printed, verified, images, bool(tables), drawings, sum(len(l.text) for l in content)))
        if not content:
            builder.diagnostics.append(Diagnostic("info", "empty_page", "no text on page (figure-only or blank)", index))

        events: list[tuple[float, int, object]] = [(l.y0, 1, l) for l in content if not any(_inside(l, r) for r, _ in tables)]
        events += [(rect.y0, 0, (rect, rows)) for rect, rows in tables]
        events.sort(key=lambda e: (round(e[0] / 2), e[1], getattr(e[2], "x0", 0)))

        pending_heading: list[Line] = []

        def emit_heading() -> None:
            if not pending_heading:
                return
            text = " ".join(l.text for l in pending_heading)
            y = pending_heading[0].y0
            pending_heading.clear()
            _heading(builder, text, index, major=True, y=y)

        for _, kind, item in events:
            if kind == 0:
                emit_heading()
                rect, rows = item  # type: ignore[misc]
                caption = None
                if builder.last_caption and builder.last_caption[1] == index and rect.y0 - builder.last_caption[2] < 60:
                    caption = builder.last_caption[0]
                builder.table(rows, index, caption)
                builder.last_caption = None
                continue
            line: Line = item  # type: ignore[assignment]
            if line.bold and line.size >= heading_size and line.x0 <= margin + 4:
                if index == 0 and line.size >= body_size * 1.45:
                    builder.add_text(line.text, index, ("document_title",))
                    continue
                if pending_heading and line.y0 - pending_heading[-1].y1 > line.size * 1.2:
                    emit_heading()
                pending_heading.append(line)
                continue
            emit_heading()
            if BANNER.match(line.text):
                if builder.current is None or "non_regulatory_banner" not in builder.current.flags:
                    builder.flush()
                    builder.start("front_matter", None, index, ["non_regulatory_banner"])
                builder.current_lines.append(line.text)
                continue
            if line.size <= footnote_size and line.x0 <= margin + 4:
                _add_footnote(builder, line.text, index)
                continue
            if line.x0 > margin + 4:
                builder.add_text(line.text, index, ("layout_text_outside_margin",))
                continue
            if line.bold and abs(line.size - body_size) < 0.8:
                if _heading(builder, line.text, index, major=False, y=line.y0):
                    continue
            _body_line(builder, line, index)
        emit_heading()

    passages = builder.finish()
    _mark_visuals(passages, pages)
    return ParsedDocument(
        parser_version=PARSER_VERSION,
        page_count=doc.page_count,
        footer_regulation=footer_reg,
        footer_version=footer_ver,
        body_size=body_size,
        left_margin=margin,
        pages=pages,
        passages=passages,
        namespace_headings=builder.namespace_headings,
        diagnostics=builder.diagnostics,
    )


def _add_footnote(builder: _Builder, text: str, page: int) -> None:
    previous = builder.passages[-1] if builder.passages else None
    for earlier in reversed(builder.passages[-12:]):
        lines = getattr(earlier, "_lines", None)
        if earlier.kind == "footnote" and earlier.pdf_pages[-1] == page and lines and " ".join(lines[0].split()) == " ".join(text.split()):
            return  # the renderer repeats a footnote once per marker on the page
    if previous is not None and previous.kind == "footnote" and getattr(previous, "_lines", None) is not None and previous.pdf_pages[-1] == page and not re.match(r"^\^?\d{1,2}\s?[A-Z]", text):
        previous._lines.append(text)  # type: ignore[attr-defined]
        return
    fn = ParsedPassage(builder._key(f"{builder.namespace}|_footnote"), builder.namespace, None, "footnote", "", [page], ["footnote"])
    fn._lines = [text]  # type: ignore[attr-defined]
    # inserted before the open paragraph is flushed, so order stays "as read"; the open paragraph keeps going
    builder.passages.append(fn)


def _heading(builder: _Builder, text: str, page: int, *, major: bool, y: float = 0.0) -> bool:
    """Handle a bold heading. Returns False when a minor bold line is just emphasised body text."""
    if major and st.GTR_RATIONALE.match(text):
        builder.set_namespace(st.RATIONALE, text, page)
        builder.in_rationale = True
        return True
    if major and st.GTR_TEXT.match(text):
        builder.set_namespace(st.MAIN, text, page)
        builder.last_label.pop(st.MAIN, None)
        return True
    if builder.in_rationale and major and not st.ANNEX_HEADING.match(text):
        # two numbering systems interleave in a GTR rationale; its headings are not paragraph labels
        builder.start("heading", None, page)
        builder.current_lines.append(text)
        builder.previous_line = ""
        return True
    if major and builder.namespace == st.MAIN and not builder.last_label.get(st.MAIN) and st.INTRODUCTION_HEADING.match(text):
        builder.set_namespace(st.INTRODUCTION, text, page)
        return True
    if major and builder.namespace == st.INTRODUCTION and (st.LABEL_LINE.match(text) or st.MAJOR_LABEL_NODOT.match(text) or st.SCOPE_HEADING.match(text)):
        builder.flush()
        builder.namespace = st.MAIN  # the numbered body starts after an introduction (R148, R171)
        builder.enclosing_label = None
    nodot = st.MAJOR_LABEL_NODOT.match(text) if major else None
    if nodot and not st.ANNEX_HEADING.match(text):
        previous = builder.last_label.get(builder.namespace)
        candidate = st.parse_label(nodot["label"] + ".")
        if (previous is None and candidate == (1,)) or (previous is not None and st.judge_label(candidate, previous, "", nodot["rest"]).flags == ()):
            builder.label(nodot["label"] + ".", nodot["rest"], page, ("label_without_dot", "heading"))
            return True
    split = st.ANNEX_SPLIT_DIGITS.match(text) if major else None
    if split and st.namespace_label(split["a"]) in builder.namespace_headings and st.namespace_label(split["a"] + split["b"]) not in builder.namespace_headings:
        text = f"Annex {split['a']}{split['b']} {split['rest']}"
    annex = st.ANNEX_HEADING.match(text)
    if annex and (major or annex.group("part") or annex.group("appx_kw")):
        builder.annex = annex.group("annex").upper()
        appendix = annex.group("appendix") or (" " if annex.group("appx_kw") else None)  # " " = unnumbered appendix
        namespace = st.namespace_label(builder.annex, annex.group("part"), appendix)
        builder.set_namespace(namespace, text, page)
        return True
    appendix = st.APPENDIX_HEADING.match(text)
    if appendix and major:
        parent = st.namespace_parts(builder.namespace)
        namespace = st.namespace_label(parent.get("annex"), parent.get("part"), appendix.group("appendix")) if parent.get("annex") else f"Appendix {appendix.group('appendix')}"
        builder.set_namespace(namespace, text, page)
        return True
    part = st.PART_HEADING.match(text)
    if part and (major or len(text) < 160):
        parent = st.namespace_parts(builder.namespace)
        if not parent.get("annex"):
            # A main-body Part (R89 "PART II ...", R67, R163) is a heading, not a numbering division: paragraph
            # numbers run on across Parts (Part I 3-10, Part II 11-18), so they stay in their namespace and cite as
            # "13.1". A document whose main-body Parts restarted numbering would show up as duplicate_label.
            builder.start("heading", None, page, ["part_heading"])
            builder.current_lines.append(text)
            builder.previous_line = ""
            return True
        namespace = st.namespace_label(parent.get("annex"), part.group("part"), None)
        builder.set_namespace(namespace, text, page)
        return True
    if st.LABEL_LINE.match(text):
        fake = Line(text, 0, 0, 0, 0, 0, True, page)
        return _body_line(builder, fake, page, heading=True)
    if st.CAPTION.match(text):
        builder.caption(text, page, y)
        return True
    if major:
        # an unnumbered major heading such as "Scope": its own heading passage in the current namespace
        builder.start("heading", None, page, ["scope_heading"] if st.SCOPE_HEADING.match(text) else None)
        builder.current_lines.append(text)
        builder.previous_line = ""
        return True
    return False


def _body_line(builder: _Builder, line: Line, page: int, heading: bool = False) -> bool:
    match = st.LABEL_LINE.match(line.text)
    previous = builder.last_label.get(builder.namespace)
    if match:
        candidate = st.parse_label(match["label"])
        verdict = st.judge_label(candidate, previous, builder.previous_line, match["rest"])
        if verdict.accept:
            builder.label(match["label"], match["rest"], page, verdict.flags + (("heading",) if heading else ()))
            for flag in verdict.flags:
                after = ".".join(map(str, previous)) if previous else "start"
                builder.diagnostics.append(Diagnostic("info" if flag == "label_gap" else "warning", flag, f"{builder.namespace} {match['label']} after {after}", page))
            return True
    else:
        nodot = st.LABEL_LINE_NODOT.match(line.text)
        if nodot and previous is not None and st.judge_label(st.parse_label(nodot["label"]), previous, builder.previous_line, nodot["rest"]).flags == () \
                and st.judge_label(st.parse_label(nodot["label"]), previous, builder.previous_line, nodot["rest"]).accept:
            builder.label(nodot["label"] + ".", nodot["rest"], page, ("label_without_dot",) + (("heading",) if heading else ()))
            return True
    # an unemphasised line is a caption only when short and not a sentence ("Table 3 lists the ..." stays text)
    if st.CAPTION.match(line.text) and len(line.text) <= 100 and not line.text.rstrip().endswith((".", ":", ";")):
        builder.caption(line.text, page, line.y0)
        return True
    builder.add_text(line.text, page)
    return True


def _mark_visuals(passages: list[ParsedPassage], pages: list[PageInfo]) -> None:
    visual = {p.pdf_page_index for p in pages if p.has_images or p.has_tables or p.drawing_count > 40}
    for passage in passages:
        if visual.intersection(passage.pdf_pages) and "page_has_visual_content" not in passage.flags:
            passage.flags.append("page_has_visual_content")
        if len(set(passage.pdf_pages)) > 1 and "spans_pages" not in passage.flags:
            passage.flags.append("spans_pages")
