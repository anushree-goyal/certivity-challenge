"""Turn a ParsedDocument into the rows the corpus stores: public IDs, sections, ancestry, definitions, references.

Section granularity is adaptive. Every namespace (main body, Annex 4, Annex 6 Part 2, Annex 4 Appendix 1, ...) has
a root section. Within it, a paragraph whose whole subtree (sub-paragraphs, tables, footnotes) fits
MAX_SECTION_CHARS becomes one section; a larger paragraph keeps only its own text and its children become sections.
So "6.10. Rear position lamp" in R48 is one section with all of 6.10.x, while "6. Individual specifications" is split.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import structure as st
from .parse import ParsedDocument, ParsedPassage
from .registry import DocumentSpec

MAX_SECTION_CHARS = 6000  # a section bundle should be a manageable extraction unit


@dataclass
class SectionRow:
    section_id: str
    document_id: str
    parent_section_id: str | None
    namespace: str
    raw_label: str | None
    paragraph_label: str | None
    depth: int  # 0 = namespace root
    heading: str
    order: int
    heading_passage_id: str | None = None
    flags: list[str] = field(default_factory=list)


@dataclass
class PassageRow:
    passage_id: str
    document_id: str
    section_id: str
    namespace: str
    raw_label: str | None
    paragraph_label: str | None
    kind: str
    text: str
    printed_start_page: int | None
    page_span: list[int]
    pdf_page_indices: list[int]
    flags: list[str]
    heading_ancestry: list[str]
    order: int
    table_rows: list[list[str]] | None


@dataclass
class DefinitionRow:
    document_id: str
    term: str
    term_norm: str
    passage_id: str


@dataclass
class ReferenceRow:
    passage_id: str
    document_id: str
    raw: str
    start: int
    end: int
    target_regulation_number: str | None
    target_namespace: str | None
    target_paragraph: str | None
    explicit_this_regulation: bool


@dataclass
class AssembledDocument:
    sections: list[SectionRow]
    passages: list[PassageRow]
    definitions: list[DefinitionRow]
    references: list[ReferenceRow]


def _slug(namespace: str) -> str:
    return namespace.replace(" ", "")


def section_id_for(document_id: str, namespace: str, label_key: tuple | None) -> str:
    tail = ".".join(map(str, label_key)) if label_key else "root"
    return f"{document_id}/sec/{_slug(namespace)}/{tail}"


def passage_id_for(document_id: str, passage: ParsedPassage) -> str:
    ns, _, rest = passage.key.partition("|")
    rest = rest.replace("#", "_dup").replace(" ", "_")
    return f"{document_id}/p/{_slug(ns)}/{rest}"


def short_heading(text: str, label: str | None) -> str:
    body = text[len(label):].strip() if label and text.startswith(label) else text.strip()
    first = re.split(r"(?<=[.:;])\s|\n", body, maxsplit=1)[0]
    return (first[:117] + "...") if len(first) > 120 else first


def _parent_key(key: tuple) -> tuple | None:
    """('A', 1, 4) -> ('A', 1); (6, 10) -> (6,); (6,) -> None (namespace root)."""
    return key[:-1] if len(st.label_numbers(key)) > 1 else None


def assemble(spec: DocumentSpec, parsed: ParsedDocument) -> AssembledDocument:
    doc_id = spec.document_id
    printed = {p.pdf_page_index: p.printed_page for p in parsed.pages}

    # pass 1: passage rows, each attached to a paragraph-tree node (namespace, label key or None for the root)
    titles: dict[str, str] = {}
    by_label: dict[tuple[str, tuple], PassageRow] = {}
    rows: list[PassageRow] = []
    node_of: dict[str, tuple[str, tuple | None]] = {}
    current: dict[str, tuple | None] = {}
    for passage in parsed.passages:
        ns = passage.namespace
        label_key = st.parse_label(passage.label) if passage.label else None
        if passage.kind == "heading":
            node = None
        elif label_key is not None:
            node = label_key
        else:
            node = current.get(ns)
        current[ns] = node
        pid = passage_id_for(doc_id, passage)
        pages_printed = [printed.get(i) for i in passage.pdf_pages]
        flags = list(passage.flags)
        if not passage.label and ns != st.MAIN and passage.kind in ("intro", "heading", "paragraph"):
            flags.append("label_from_namespace")
        row = PassageRow(
            passage_id=pid,
            document_id=doc_id,
            section_id="",
            namespace=ns,
            raw_label=passage.label,
            paragraph_label=st.citation_label(ns, passage.label) if (passage.label or ns != st.MAIN) else None,
            kind=passage.kind,
            text=passage.text,
            printed_start_page=pages_printed[0] if pages_printed else None,
            page_span=sorted({p for p in pages_printed if p is not None}),
            pdf_page_indices=list(passage.pdf_pages),
            flags=flags,
            heading_ancestry=[],
            order=passage.order,
            table_rows=passage.table_rows,
        )
        rows.append(row)
        node_of[pid] = (ns, node)
        if passage.title and "\n" in passage.text:
            titles[pid] = passage.title
        if passage.kind == "paragraph" and label_key is not None and "continuation_after_table" not in flags:
            by_label.setdefault((ns, label_key), row)

    # pass 2: the paragraph tree per namespace, with implicit ancestors for labels whose parent never appeared
    own: dict[tuple[str, tuple | None], list[PassageRow]] = {}
    children: dict[tuple[str, tuple | None], list[tuple | None]] = {}
    first_order: dict[tuple[str, tuple | None], int] = {}
    namespaces: list[str] = [st.MAIN]
    for row in rows:
        ns, node = node_of[row.passage_id]
        if ns not in namespaces:
            namespaces.append(ns)
        own.setdefault((ns, node), []).append(row)
        key = node
        while key is not None:
            parent = _parent_key(key)
            first_order[(ns, key)] = min(first_order.get((ns, key), row.order), row.order)
            siblings = children.setdefault((ns, parent), [])
            if key not in siblings:
                siblings.append(key)
            key = parent
        first_order[(ns, None)] = min(first_order.get((ns, None), row.order), row.order)

    size_cache: dict[tuple[str, tuple | None], int] = {}

    def subtree_size(ns: str, key: tuple | None) -> int:
        if (ns, key) not in size_cache:
            size_cache[(ns, key)] = sum(len(r.text) for r in own.get((ns, key), [])) + sum(subtree_size(ns, c) for c in children.get((ns, key), []))
        return size_cache[(ns, key)]

    # pass 3: sections. A node whose whole subtree fits MAX_SECTION_CHARS is one section; a larger node keeps its
    # own passages and its children become sections of their own (recursively).
    sections: dict[str, SectionRow] = {}

    def root_section(ns: str) -> SectionRow:
        sid = section_id_for(doc_id, ns, None)
        if sid not in sections:
            parts = st.namespace_parts(ns)
            parent = None
            if ns != st.MAIN:
                if parts.get("annex") and (parts.get("part") or parts.get("appendix")):
                    parent = root_section(st.namespace_label(parts["annex"])).section_id
                else:
                    parent = root_section(st.MAIN).section_id
            heading = parsed.namespace_headings.get(ns) or (spec.version_title or spec.regulation if ns == st.MAIN else ns)
            flags = [] if ns == st.MAIN or ns in parsed.namespace_headings else ["implicit_namespace"]
            heads = [r for r in own.get((ns, None), []) if r.kind == "heading"]
            sections[sid] = SectionRow(sid, doc_id, parent, ns, None, st.citation_label(ns, None), 0, heading,
                                       first_order.get((ns, None), 0), heads[0].passage_id if heads else None, flags)
        return sections[sid]

    def assign_subtree(ns: str, key: tuple | None, section: SectionRow) -> None:
        for row in own.get((ns, key), []):
            row.section_id = section.section_id
        for child in children.get((ns, key), []):
            assign_subtree(ns, child, section)

    def label_section(ns: str, key: tuple, parent: SectionRow) -> SectionRow:
        raw = ".".join(map(str, key)) + "."
        head = by_label.get((ns, key))
        flags = [] if head else ["implicit_section"]
        heading = (titles.get(head.passage_id) or short_heading(head.text, head.raw_label)) if head else raw
        row = SectionRow(section_id_for(doc_id, ns, key), doc_id, parent.section_id, ns, raw, st.citation_label(ns, raw),
                         len(st.label_numbers(key)), heading, first_order[(ns, key)], head.passage_id if head else None, flags)
        sections[row.section_id] = row
        return row

    def place(ns: str, key: tuple | None, section: SectionRow) -> None:
        for row in own.get((ns, key), []):
            row.section_id = section.section_id
        for child in children.get((ns, key), []):
            child_section = label_section(ns, child, section)
            if subtree_size(ns, child) <= MAX_SECTION_CHARS:
                assign_subtree(ns, child, child_section)
            else:
                place(ns, child, child_section)

    for ns in namespaces:
        top = root_section(ns)
        if ns != st.MAIN and subtree_size(ns, None) <= MAX_SECTION_CHARS:
            assign_subtree(ns, None, top)
        else:
            place(ns, None, top)
    ordered = sorted(sections.values(), key=lambda sec: (sec.order, sec.depth))
    for i, section in enumerate(ordered):
        section.order = i

    # heading ancestry: namespace chain, then every labelled ancestor paragraph that exists
    for row in rows:
        chain: list[str] = []
        sec = sections[row.section_id]
        lineage = []
        while sec is not None:
            lineage.append(sec)
            sec = sections.get(sec.parent_section_id) if sec.parent_section_id else None
        for sec in reversed(lineage):
            if sec.depth == 0:
                chain.append(sec.heading)
        if row.raw_label:
            key = st.parse_label(row.raw_label)
            prefix = tuple(p for p in key[:1] if not isinstance(p, int))
            numbers = st.label_numbers(key)
            for n in range(1, len(numbers)):
                ancestor = by_label.get((row.namespace, prefix + numbers[:n]))
                if ancestor is not None:
                    chain.append(f"{ancestor.raw_label} {titles.get(ancestor.passage_id) or short_heading(ancestor.text, ancestor.raw_label)}")
        row.heading_ancestry = chain

    definitions: list[DefinitionRow] = []
    for row in rows:
        if row.kind != "paragraph":
            continue
        body = row.text[len(row.raw_label):].strip() if row.raw_label and row.text.startswith(row.raw_label) else row.text
        for term in st.definition_terms(body):
            definitions.append(DefinitionRow(doc_id, term, normalise_term(term), row.passage_id))

    references: list[ReferenceRow] = []
    for row in rows:
        if row.kind == "footnote" and "non_regulatory_banner" in row.flags:
            continue
        for cand in st.find_references(row.text):
            references.append(
                ReferenceRow(row.passage_id, doc_id, cand.raw, cand.start, cand.end, cand.target_regulation_number, cand.target_annex, cand.target_paragraph, cand.explicit_this_regulation)
            )

    return AssembledDocument(sorted(sections.values(), key=lambda s: s.order), rows, definitions, references)


def normalise_term(term: str) -> str:
    return " ".join(re.sub(r"[‑–—-]", "-", term).casefold().split())
