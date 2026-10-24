"""Read-only CorpusReader bound to one completed snapshot.

Connections are opened per thread (SQLite objects must stay in the thread that created them) with
``mode=ro&immutable=1``, so nothing here can modify the snapshot.
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Sequence

from . import structure as st
from ._contracts import (
    Document,
    DocumentRole,
    PageAsset,
    ReferenceRequest,
    ReferenceResolution,
    ReferenceStatus,
    Section,
    SectionBundle,
    SourcePassage,
)
from .config import CorpusPaths, default_paths
from .render import DEFAULT_DPI, render_page
from .store import current_snapshot_id

MAX_DEFINITION_CONTEXT = 20
LARGE_SECTION_CHARS = 12000
_SCOPE_HEADING = re.compile(r"^(scope|field of application|application)\b(?!\s+for\b)", re.IGNORECASE)
_DEFINITIONS_HEADING = re.compile(r"^definitions?\b", re.IGNORECASE)
_SUB_ITEM = re.compile(r"^(?P<paragraph>.*?\d)\.?\s*\((?P<item>[a-z]{1,2}|[ivx]{1,5})\)$", re.IGNORECASE)
_FOOTNOTE_START = re.compile(r"^(\(?[a-z]\)|\*+|\^?\d{1,2}/|\^\d{1,2}|\(\d{1,2}\)|Notes?\b)", re.IGNORECASE)


class CorpusNotReady(RuntimeError):
    """No completed snapshot is available; run the corpus build step."""


class UnknownId(KeyError):
    pass


class SqliteCorpusReader:
    def __init__(self, snapshot_dir: Path, paths: CorpusPaths) -> None:
        manifest_path = snapshot_dir / "manifest.json"
        if not manifest_path.is_file():
            raise CorpusNotReady(f"{snapshot_dir} is not a completed snapshot")
        self.manifest = json.loads(manifest_path.read_text())
        if not self.manifest.get("complete"):
            raise CorpusNotReady(f"{snapshot_dir} is incomplete")
        self.snapshot_id: str = self.manifest["snapshot_id"]
        self._db_uri = f"file:{(snapshot_dir / 'corpus.sqlite').as_posix()}?mode=ro&immutable=1"
        self._paths = paths
        self._package_root = Path(self.manifest.get("package_root") or paths.package_root)
        self._local = threading.local()

    # --- plumbing -----------------------------------------------------------------------------

    @property
    def _db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_uri, uri=True)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _passage(self, row: sqlite3.Row, extra_flags: list[str] | None = None) -> SourcePassage:
        flags = list(dict.fromkeys(json.loads(row["flags"]) + [f"kind:{row['kind']}"] + (extra_flags or [])))
        is_table = row["kind"] == "table"
        start = row["printed_start_page"]
        return SourcePassage(
            passage_id=row["passage_id"],
            document_id=row["document_id"],
            section_id=row["section_id"],
            text=row["text"],
            paragraph_label=row["paragraph_label"],
            heading_ancestry=tuple(json.loads(row["heading_ancestry"])),
            printed_start_page=str(start) if start is not None else None,
            page_span=tuple(str(n) for n in json.loads(row["page_span"])),
            pdf_page_indices=tuple(json.loads(row["pdf_page_indices"])),
            namespace=st.machine_namespace(row["namespace"]),
            parsing_flags=tuple(flags),
            is_table=is_table,
            table_context=self._table_context(row) if is_table else None,
        )

    def _section(self, row: sqlite3.Row) -> Section:
        ids = tuple(r[0] for r in self._db.execute("SELECT passage_id FROM passages WHERE section_id = ? ORDER BY ord", (row["section_id"],)))
        return Section(
            section_id=row["section_id"],
            document_id=row["document_id"],
            parent_section_id=row["parent_section_id"],
            heading=row["heading"],
            namespace=st.machine_namespace(row["namespace"]),
            paragraph_label=row["paragraph_label"],
            primary_passage_ids=ids,
            depth=row["depth"],
            ordinal=row["ord"],
            parsing_flags=tuple(json.loads(row["flags"])),
        )

    def _document(self, row: sqlite3.Row) -> Document:
        return Document(
            document_id=row["document_id"],
            regulation=row["regulation"],
            title=row["title"],
            role=DocumentRole(row["role"]),
            version_label=row["version_label"],
            filename=row["filename"],
            checksum=row["checksum"],
            snapshot_id=self.snapshot_id,
            page_count=row["page_count"],
            index_entry=json.loads(row["index_entry"]),
        )

    def _doc_row(self, document_id: str) -> sqlite3.Row:
        row = self._db.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
        if row is None:
            raise UnknownId(f"document {document_id!r} is not in snapshot {self.snapshot_id}")
        return row

    # --- CorpusReader -------------------------------------------------------------------------

    def list_documents(self, role: DocumentRole | str | None = DocumentRole.BASE) -> list[Document]:
        """Documents of that role (None = every imported role), in index.json order."""
        if role is None:
            rows = self._db.execute("SELECT * FROM documents")
        else:
            rows = self._db.execute("SELECT * FROM documents WHERE role = ?", (getattr(role, "value", role),))
        rows = sorted(rows, key=lambda r: (r["index_order"], r["filename"]))
        return [self._document(r) for r in rows]

    def get_document(self, document_id: str) -> Document:
        return self._document(self._doc_row(document_id))

    def read_scope(self, document_id: str) -> list[SourcePassage]:
        """Scope, application and definition passages used for applicability: title/front matter, the main-body
        Scope clause with its sub-paragraphs, then the main-body Definitions clause."""
        scope = self._scope_passages(document_id)
        seen = {p.passage_id for p in scope}
        definitions = [p for p in self._definition_clause(document_id) if p.passage_id not in seen]
        return scope + definitions

    def _definition_clause(self, document_id: str) -> list[SourcePassage]:
        sections = self._db.execute(
            "SELECT * FROM sections WHERE document_id = ? AND namespace = 'main' AND depth = 1 ORDER BY ord", (document_id,)
        ).fetchall()
        out: list[SourcePassage] = []
        for section in sections:
            if _DEFINITIONS_HEADING.match(section["heading"]):
                for sid in self._descendant_section_ids(section["section_id"]):
                    out += [self._passage(r) for r in self._db.execute("SELECT * FROM passages WHERE section_id = ? ORDER BY ord", (sid,))]
        return out

    def _scope_passages(self, document_id: str) -> list[SourcePassage]:
        self._doc_row(document_id)
        sections = self._db.execute(
            "SELECT * FROM sections WHERE document_id = ? AND namespace = 'main' AND depth = 1 ORDER BY ord", (document_id,)
        ).fetchall()
        scope = [s for s in sections if _SCOPE_HEADING.match(s["heading"])]
        rows = list(self._db.execute(
            "SELECT * FROM passages WHERE document_id = ? AND (kind = 'front_matter' OR (kind = 'heading' AND namespace = 'main' AND flags LIKE '%scope_heading%'))"
            " AND flags NOT LIKE '%non_regulatory_banner%' ORDER BY ord",
            (document_id,),
        ))
        if any("scope_heading" in json.loads(r["flags"]) for r in rows):
            return [self._passage(r) for r in rows]
        if not scope:
            first = self._db.execute("SELECT * FROM passages WHERE document_id = ? AND kind = 'paragraph' ORDER BY ord LIMIT 6", (document_id,)).fetchall()
            return [self._passage(r) for r in rows] + [self._passage(r, ["scope_section_not_detected"]) for r in first]
        out = [self._passage(r) for r in rows]
        for section in scope:
            for sid in self._descendant_section_ids(section["section_id"]):
                out += [self._passage(r) for r in self._db.execute("SELECT * FROM passages WHERE section_id = ? ORDER BY ord", (sid,))]
        return out

    def list_sections(self, document_id: str) -> list[Section]:
        self._doc_row(document_id)
        return [self._section(r) for r in self._db.execute("SELECT * FROM sections WHERE document_id = ? ORDER BY ord", (document_id,))]

    def read_section(self, section_id: str, include_context: bool = True) -> SectionBundle:
        row = self._db.execute("SELECT * FROM sections WHERE section_id = ?", (section_id,)).fetchone()
        if row is None:
            raise UnknownId(f"section {section_id!r} is not in snapshot {self.snapshot_id}")
        section = self._section(row)
        primary_rows = self._db.execute("SELECT * FROM passages WHERE section_id = ? ORDER BY ord", (section_id,)).fetchall()
        primary = [self._passage(r) for r in primary_rows]
        context: list[SourcePassage] = []
        if include_context:
            context = self._context_for(row, primary)
        pages = sorted({i for p in primary for i in p.pdf_page_indices})
        visual: list[int] = []
        if pages:
            marks = ",".join("?" * len(pages))
            visual = [r[0] for r in self._db.execute(
                f"SELECT pdf_page_index FROM pages WHERE document_id = ? AND pdf_page_index IN ({marks}) AND (has_images OR has_tables OR drawing_count > 40) ORDER BY pdf_page_index",
                (row["document_id"], *pages),
            )]
        notes: list[str] = []
        size = sum(len(p.text) for p in primary)
        if size > LARGE_SECTION_CHARS:
            notes.append(f"large section ({size} characters): a long table or form without paragraph numbers to split on")
        if "implicit_section" in section.parsing_flags:
            notes.append("section number inferred from its sub-paragraphs; the numbered heading paragraph itself was not found")
        return SectionBundle(section=section, primary_passages=tuple(primary), context_passages=tuple(context), visual_pdf_page_indices=tuple(visual), notes=tuple(notes))

    def get_passages(self, passage_ids: Sequence[str]) -> list[SourcePassage]:
        passage_ids = list(passage_ids)
        if not passage_ids:
            return []
        found: dict[str, sqlite3.Row] = {}
        for chunk in range(0, len(passage_ids), 500):
            part = passage_ids[chunk: chunk + 500]
            marks = ",".join("?" * len(part))
            for r in self._db.execute(f"SELECT * FROM passages WHERE passage_id IN ({marks})", part):
                found[r["passage_id"]] = r
        # contract: unknown IDs are omitted, not invented; callers compare requested and returned IDs
        return [self._passage(found[p]) for p in dict.fromkeys(passage_ids) if p in found]

    def resolve_reference(self, request: ReferenceRequest) -> ReferenceResolution:
        status, ids, explanation = self._resolve(request)
        return self._resolution(request, status, ids, explanation)

    def _resolution(self, request: ReferenceRequest, status: str, ids: list[str], explanation: str) -> ReferenceResolution:
        doc_id = section_id = None
        if ids:
            marks = ",".join("?" * len(ids))
            rows = self._db.execute(f"SELECT DISTINCT document_id, section_id FROM passages WHERE passage_id IN ({marks})", ids).fetchall()
            if len({r["document_id"] for r in rows}) == 1:
                doc_id = rows[0]["document_id"]
            if len({r["section_id"] for r in rows}) == 1:
                section_id = rows[0]["section_id"]
        return ReferenceResolution(
            request=request,
            status=ReferenceStatus(status),
            explanation=explanation,
            candidate_passage_ids=tuple(ids),
            resolved_document_id=doc_id,
            resolved_section_id=section_id,
        )

    def _resolve(self, request: ReferenceRequest) -> tuple[str, list[str], str]:
        origin = self._db.execute("SELECT * FROM passages WHERE passage_id = ?", (request.originating_passage_id,)).fetchone()
        if origin is None:
            return ("missing", [], f"originating passage {request.originating_passage_id!r} is not in snapshot {self.snapshot_id}")
        regulation, namespace, paragraph, this_reg = self._reference_hints(request)
        # "4.8.3.1 (a)": a sub-item lives inside its paragraph's passage, so resolve the paragraph and say which part
        sub = _SUB_ITEM.match(paragraph) if paragraph else None
        if sub:
            paragraph = sub.group("paragraph")
        status, ids, explanation = self._resolve_target(request, origin, regulation, namespace, paragraph, this_reg)
        if sub:
            item = sub.group("item")
            if status == "ambiguous" and len(ids) > 1:
                # a label the source splits into sub-items ("3.1.(A) ...", "3.1.(B) ..."): the named one decides
                pattern = re.compile(rf"^\S+\s*\({re.escape(item)}\)", re.IGNORECASE)
                named = [p.passage_id for p in self.get_passages(ids) if pattern.match(p.text)]
                if len(named) == 1:
                    return "resolved", named, explanation + f"; of these, only one passage is sub-item ({item}) itself"
            explanation += f"; the reference names sub-item ({item}), which is part of this paragraph's text"
        return status, ids, explanation

    def _resolve_target(self, request: ReferenceRequest, origin: sqlite3.Row, regulation: str | None, namespace: str | None,
                        paragraph: str | None, this_reg: bool) -> tuple[str, list[str], str]:
        origin_doc = self._doc_row(origin["document_id"])

        # 1. which document
        if regulation is not None:
            number = st.regulation_number_from_hint(regulation)
            target_reg = regulation if number is None else f"UN R{number}"
            if target_reg != origin_doc["regulation"]:
                docs = self._db.execute("SELECT * FROM documents WHERE regulation = ? AND role = 'base' ORDER BY filename", (target_reg,)).fetchall()
                if not docs:
                    return ("outside_corpus", [], f"{target_reg} is not an allowed regulation in this snapshot; reference {request.raw_reference!r} cannot be followed")
                if len(docs) > 1 and paragraph is None and namespace is None:
                    ids = [self._first_passage(d["document_id"]) for d in docs]
                    return ("ambiguous", [i for i in ids if i], f"{target_reg} base version has {len(docs)} files; choose one")
                target_docs = docs
                cross = True
            else:
                target_docs, cross = [origin_doc], False
        else:
            target_docs, cross = [origin_doc], False

        # tables and figures are addressed by caption ("Table 4", "Figure A3-I"), not by paragraph label
        caption = st.CAPTION.match(paragraph) if paragraph else None
        if caption:
            return self._caption_targets(request, origin, target_docs, namespace, caption)

        # 2. whole-regulation reference
        if paragraph is None and namespace is None:
            doc = target_docs[0]
            scope = [p.passage_id for p in self.read_scope(doc["document_id"]) if "scope_section_not_detected" not in p.parsing_flags]
            note = "series of amendments named in the reference may differ from the base version in this corpus; " if cross else ""
            return (
                "resolved" if scope else "ambiguous", scope,
                f"whole-regulation reference to {doc['regulation']} {doc['version_label']} (base); {note}candidates are its scope passages. "
                "A reference alone does not establish that the whole regulation applies.",
            )

        # 3. which namespaces to search
        origin_ns = origin["namespace"]
        if namespace == "this annex":
            parts = st.namespace_parts(origin_ns)
            if not parts.get("annex"):
                return ("ambiguous", [], f"{request.raw_reference!r} says 'this annex' but the originating passage is in {origin_ns}")
            annex_root = st.namespace_label(parts["annex"])
            namespaces = [origin_ns] + ([annex_root] if annex_root != origin_ns else [])
            prefer_first = True
        elif namespace is not None:
            namespaces, prefer_first = [namespace], True
        elif cross or this_reg or origin_ns == st.MAIN:
            namespaces, prefer_first = [st.MAIN], True
        else:
            namespaces, prefer_first = [st.MAIN, origin_ns], False

        # 4. look the label up
        candidates: list[tuple[str, sqlite3.Row]] = []
        for doc in target_docs:
            for ns in namespaces:
                if paragraph is None:
                    head = self._db.execute(
                        "SELECT p.* FROM sections s JOIN passages p ON p.passage_id = s.heading_passage_id WHERE s.document_id = ? AND s.namespace = ? AND s.depth = 0",
                        (doc["document_id"], ns),
                    ).fetchone()
                    if head is None:
                        head = self._db.execute("SELECT * FROM passages WHERE document_id = ? AND namespace = ? ORDER BY ord LIMIT 1", (doc["document_id"], ns)).fetchone()
                    if head is not None:
                        candidates.append((ns, head))
                    continue
                for label in _paragraph_targets(paragraph):
                    rows = self._db.execute(
                        "SELECT * FROM passages WHERE document_id = ? AND namespace = ? AND raw_label = ? AND kind = 'paragraph' AND flags NOT LIKE '%continuation_after_table%' ORDER BY ord",
                        (doc["document_id"], ns, label + "."),
                    ).fetchall()
                    candidates += [(ns, r) for r in rows]
        where = ", ".join(namespaces)
        target_name = f"{target_docs[0]['regulation']} {target_docs[0]['version_label']}"
        if not candidates:
            near = self._nearest_existing(target_docs[0]["document_id"], namespaces[0], paragraph) if paragraph else None
            hint = f"; nearest existing ancestor is {near}" if near else ""
            return ("missing", [], f"{request.raw_reference!r}: no paragraph {paragraph or ''} in {where} of {target_name}{hint}")
        found_ns = {ns for ns, _ in candidates}
        ids = list(dict.fromkeys(r["passage_id"] for _, r in candidates))
        duplicates = any("duplicate_label" in json.loads(r["flags"]) for _, r in candidates)
        basis = ""
        if len(found_ns) > 1 and not prefer_first:
            # "paragraph 6. above" written inside Annex 14: if only the originating division has a match on that
            # side of the reference in reading order, the direction word identifies it. Otherwise do not guess.
            direction = re.search(r"\b(above|below)\b", request.raw_reference, re.IGNORECASE)
            if direction:
                above = direction.group(1).lower() == "above"
                local = [r for ns, r in candidates if ns == origin_ns and (r["ord"] < origin["ord"] if above else r["ord"] > origin["ord"])]
                if local:
                    found_ns, ids = {origin_ns}, [r["passage_id"] for r in local]
                    candidates = [(origin_ns, r) for r in local]
                    duplicates = any("duplicate_label" in json.loads(r["flags"]) for r in local)
                    prefer_first = True
                    basis = f"; also in main, but '{direction.group(1)}' points within {origin_ns}"
            if len(found_ns) > 1:
                return ("ambiguous", ids, f"{request.raw_reference!r} matches paragraph {paragraph} in {', '.join(sorted(found_ns))} of {target_name}; the reference does not say which")
        if prefer_first and len(found_ns) > 1:
            first_ns = next(ns for ns in namespaces if ns in found_ns)
            ids = [r["passage_id"] for ns, r in candidates if ns == first_ns]
            found_ns = {first_ns}
        is_range = "-" in (paragraph or "")
        if (len(ids) > 1 and not is_range) or duplicates:
            return ("ambiguous", ids, f"paragraph {paragraph} occurs more than once in {next(iter(found_ns))} of {target_name} (parsing flagged a duplicate label)")
        note = " (range endpoints only; intermediate paragraphs are in the same section)" if is_range else ""
        where_found = next(iter(found_ns))
        what = f"{where_found} {paragraph}" if paragraph else where_found
        searched = f" (searched {where})" if not prefer_first else ""
        return ("resolved", ids, f"{request.raw_reference!r} -> {what} of {target_name}{note}{searched}{basis}")

    def get_page_asset(self, document_id: str, pdf_page_index: int, dpi: int = DEFAULT_DPI) -> PageAsset:
        doc = self._doc_row(document_id)
        page = self._db.execute("SELECT * FROM pages WHERE document_id = ? AND pdf_page_index = ?", (document_id, pdf_page_index)).fetchone()
        if page is None:
            raise UnknownId(f"{document_id} has no pdf_page_index {pdf_page_index} (0..{doc['page_count'] - 1})")
        path = render_page(self.document_path(document_id), doc["checksum"], pdf_page_index, self._paths.renders, dpi)
        label = str(page["printed_page"]) if page["printed_verified"] else None
        width, height = _png_size(path)
        return PageAsset(document_id=document_id, pdf_page_index=pdf_page_index, printed_page=label, media_type="image/png",
                         asset_ref=str(path), width=width, height=height)

    # --- additions beyond the planned interface (server-side helpers) -------------------------

    def get_document_path(self, document_id: str) -> Path:
        """Server-only absolute path of the original supplied PDF, for the backend's /api/documents/{id}/pdf route.
        Raises UnknownId for an unknown document and FileNotFoundError if the file is missing or changed."""
        return self.document_path(document_id)

    def document_path(self, document_id: str) -> Path:
        """Server-only absolute path of the original supplied PDF (for the backend's /pdf route)."""
        doc = self._doc_row(document_id)
        path = self._package_root / doc["package_relpath"]
        if not path.is_file() or path.stat().st_size != doc["size_bytes"]:
            raise FileNotFoundError(f"source file for {document_id} is missing or changed: {path}")
        return path

    def list_reference_candidates(self, passage_ids: Sequence[str]) -> list[ReferenceRequest]:
        """References detected in these passages at import time, as ready-to-resolve requests."""
        passage_ids = list(passage_ids)
        out: list[ReferenceRequest] = []
        seen: set[tuple] = set()
        for chunk in range(0, len(passage_ids), 500):
            part = passage_ids[chunk: chunk + 500]
            marks = ",".join("?" * len(part))
            for r in self._db.execute(f"SELECT * FROM reference_candidates WHERE passage_id IN ({marks}) ORDER BY passage_id, start", part):
                reg = f"UN R{r['target_regulation_number']}" if r["target_regulation_number"] else None
                key = (r["passage_id"], r["raw"], reg, r["target_namespace"], r["target_paragraph"])
                if key not in seen:
                    seen.add(key)
                    ns = r["target_namespace"]
                    out.append(ReferenceRequest(
                        originating_passage_id=r["passage_id"], raw_reference=r["raw"], target_regulation=reg,
                        target_namespace=st.machine_namespace(ns) if ns and ns != "this annex" else ns,
                        target_paragraph=r["target_paragraph"], origin_document_id=r["document_id"],
                    ))
        return out

    def search(self, query: str, document_id: str | None = None, limit: int = 20) -> list[SourcePassage]:
        """Full-text navigation aid. Not a coverage mechanism: use list_sections for systematic reading."""
        terms = " ".join(f'"{t}"' for t in re.findall(r"[\w.-]+", query))
        if not terms:
            return []
        sql = "SELECT p.* FROM passages_fts f JOIN passages p ON p.passage_id = f.passage_id WHERE passages_fts MATCH ?"
        args: list = [terms]
        if document_id:
            sql += " AND f.document_id = ?"
            args.append(document_id)
        sql += " ORDER BY rank LIMIT ?"
        args.append(limit)
        return [self._passage(r) for r in self._db.execute(sql, args)]

    def diagnostics(self, document_id: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM import_diagnostics", []
        if document_id:
            sql, args = sql + " WHERE document_id = ?", [document_id]
        return [dict(r) for r in self._db.execute(sql, args)]

    def status(self) -> dict:
        counts = self.manifest.get("counts", {})
        return {
            "ready": True,
            "snapshot_id": self.snapshot_id,
            "created_at": self.manifest.get("created_at"),
            "roles": self.manifest.get("roles"),
            "documents": len(self.manifest.get("documents", [])),
            "not_downloaded": self.manifest.get("not_downloaded", []),
            "checksum_mismatch": self.manifest.get("checksum_mismatch", []),
            "parse_failed": self.manifest.get("parse_failed", {}),
            "counts": counts,
            "parser_version": self.manifest.get("parser_version"),
            "package_commit": self.manifest.get("package_commit"),
        }

    # --- internals ----------------------------------------------------------------------------

    def _reference_hints(self, request: ReferenceRequest) -> tuple[str | None, str | None, str | None, bool]:
        regulation, namespace, paragraph = request.target_regulation, request.target_namespace, request.target_paragraph
        this_reg = bool(re.search(r"this\s+Regulation", request.raw_reference, re.IGNORECASE))
        if regulation is None and namespace is None and paragraph is None:
            parsed = st.find_references(request.raw_reference)
            if parsed:
                first = parsed[0]
                regulation = f"UN R{first.target_regulation_number}" if first.target_regulation_number else None
                namespace, paragraph = first.target_annex, first.target_paragraph
                this_reg = this_reg or first.explicit_this_regulation
        if namespace and namespace != "this annex":
            namespace = st.human_namespace(namespace)  # accepts "annex:6/part:6" and "Annex 6, Part 6"
        if paragraph:
            paragraph = paragraph.strip().rstrip(".")
            match = re.match(r"^(?:Annex\s+\S+(?:\s+(?:Part|Appendix)\s+\S+)*\s*,?\s*)", paragraph, re.IGNORECASE)
            if match and match.group(0).strip():
                namespace = namespace or st.human_namespace(match.group(0).strip(" ,"))
                paragraph = paragraph[match.end():].strip() or None
        return regulation, namespace, paragraph, this_reg

    def _context_for(self, section_row: sqlite3.Row, primary: list[SourcePassage]) -> list[SourcePassage]:
        primary_ids = {p.passage_id for p in primary}
        context_ids: list[str] = []
        # parent chain: heading passages of every ancestor section (Annex heading, Part heading, "6. ..." clause)
        parent = section_row["parent_section_id"]
        chain: list[str] = []
        while parent:
            prow = self._db.execute("SELECT parent_section_id, heading_passage_id FROM sections WHERE section_id = ?", (parent,)).fetchone()
            if prow is None:
                break
            if prow["heading_passage_id"]:
                chain.append(prow["heading_passage_id"])
            parent = prow["parent_section_id"]
        context_ids += reversed(chain)
        # definitions used in the primary text
        text = " ".join(p.text for p in primary).casefold()
        text = re.sub(r"[‑–—-]", "-", text)
        defs = self._db.execute("SELECT term_norm, passage_id FROM definitions WHERE document_id = ?", (section_row["document_id"],)).fetchall()
        hits = [(len(d["term_norm"]), d["passage_id"]) for d in defs if d["passage_id"] not in primary_ids and re.search(rf"(?<!\w){re.escape(d['term_norm'])}(?!\w)", text)]
        hits.sort(reverse=True)
        context_ids += [pid for _, pid in hits[:MAX_DEFINITION_CONTEXT]]
        context_ids = [c for c in dict.fromkeys(context_ids) if c not in primary_ids]
        rows = {r["passage_id"]: r for r in self._db.execute(f"SELECT * FROM passages WHERE passage_id IN ({','.join('?' * len(context_ids))})", context_ids)} if context_ids else {}
        out = []
        for cid in context_ids:
            if cid in rows:
                role = "context:definition" if cid in {pid for _, pid in hits} else "context:parent"
                out.append(self._passage(rows[cid], [role]))
        return out

    def _caption_targets(self, request: ReferenceRequest, origin: sqlite3.Row, target_docs: list, namespace: str | None,
                         caption: re.Match) -> tuple[str, list[str], str]:
        kind, name = caption.group("kind").lower(), caption.group("name").lower()
        wanted = f"{kind.capitalize()} {caption.group('name')}"
        if namespace == "this annex":
            annex = st.namespace_parts(origin["namespace"]).get("annex")
            namespace = st.namespace_label(annex) if annex else origin["namespace"]
        hits: list[sqlite3.Row] = []
        for doc in target_docs:
            for row in self._db.execute(
                "SELECT * FROM passages WHERE document_id = ? AND kind IN ('table', 'caption') ORDER BY ord", (doc["document_id"],)
            ):
                found = st.CAPTION.match(row["text"].split("\n", 1)[0])
                if not found or found.group("kind").lower() != kind or found.group("name").lower() != name:
                    continue
                if namespace and not (row["namespace"] == namespace or row["namespace"].startswith(namespace + " ")):
                    continue  # "Table A2-1 of Annex 2" may sit in "Annex 2 Part A"
                hits.append(row)
        target_name = f"{target_docs[0]['regulation']} {target_docs[0]['version_label']}"
        where = f" in {namespace}" if namespace else ""
        if not hits:
            return ("missing", [], f"{request.raw_reference!r}: no {wanted}{where} of {target_name}")
        if len({h["namespace"] for h in hits}) > 1:
            local = [h for h in hits if h["namespace"] == origin["namespace"]]
            if len(local) == 1:
                hits = local
            else:
                return ("ambiguous", [h["passage_id"] for h in hits],
                        f"{request.raw_reference!r}: {wanted} occurs in {', '.join(sorted({h['namespace'] for h in hits}))} of {target_name}")
        if len(hits) > 1:
            return ("ambiguous", [h["passage_id"] for h in hits], f"{request.raw_reference!r}: {len(hits)} captions read {wanted} in {hits[0]['namespace']} of {target_name}")
        hit = hits[0]
        pages = json.loads(hit["page_span"])
        extra = " The figure itself is an image: render its page with get_page_asset." if kind == "figure" else ""
        return ("resolved", [hit["passage_id"]],
                f"{request.raw_reference!r} -> {wanted} ({hit['kind']} passage, {hit['namespace']}, page {pages[0] if pages else '?'}) of {target_name}.{extra}")

    def _table_context(self, row: sqlite3.Row) -> str | None:
        """Caption, the column-heading row, and footnote-like text from the table's page(s).

        Interim footnote rule (until footnotes are tied to their table at parse time): footnote passages on the same
        page, and short lines right after the table that start with a marker ("(a)", "a)", "*", "1/", "Note").
        Footnotes change what a cell means ("values applying at 25 °C (a)"), so over-inclusion is the safe side.
        """
        rows = json.loads(row["table_rows"]) if row["table_rows"] else []
        caption = row["text"].split("\n", 1)[0]
        header = next((r for r in rows if any(c.strip() for c in r)), None)
        parts = [caption] if caption and caption != "Table" else []
        if header:
            parts.append("Columns: " + " | ".join(header))
        pages = set(json.loads(row["pdf_page_indices"]))
        notes: list[str] = []
        for fn in self._db.execute("SELECT text, pdf_page_indices FROM passages WHERE document_id = ? AND kind = 'footnote' ORDER BY ord", (row["document_id"],)):
            if pages & set(json.loads(fn["pdf_page_indices"])):
                notes.append(fn["text"])
        after = self._db.execute(
            "SELECT text, pdf_page_indices FROM passages WHERE section_id = ? AND ord > ? AND kind IN ('paragraph', 'intro') ORDER BY ord LIMIT 3",
            (row["section_id"], row["ord"]),
        ).fetchall()
        for nxt in after:
            if not pages & set(json.loads(nxt["pdf_page_indices"])) or not _FOOTNOTE_START.match(nxt["text"]):
                break
            notes.append(nxt["text"][:400])
        if notes:
            parts.append("Footnotes: " + " / ".join(notes))
        return "\n".join(parts) or None

    def _descendant_section_ids(self, section_id: str) -> list[str]:
        out, frontier = [section_id], [section_id]
        while frontier:
            marks = ",".join("?" * len(frontier))
            frontier = [r[0] for r in self._db.execute(f"SELECT section_id FROM sections WHERE parent_section_id IN ({marks}) ORDER BY ord", frontier)]
            out += frontier
        return out

    def _first_passage(self, document_id: str) -> str | None:
        row = self._db.execute("SELECT passage_id FROM passages WHERE document_id = ? ORDER BY ord LIMIT 1", (document_id,)).fetchone()
        return row[0] if row else None

    def _nearest_existing(self, document_id: str, namespace: str, paragraph: str) -> str | None:
        parts = paragraph.split("-")[0].split(".")
        for n in range(len(parts) - 1, 0, -1):
            label = ".".join(parts[:n]) + "."
            if self._db.execute("SELECT 1 FROM passages WHERE document_id = ? AND namespace = ? AND raw_label = ?", (document_id, namespace, label)).fetchone():
                return st.citation_label(namespace, label)
        return None


def _paragraph_targets(paragraph: str) -> list[str]:
    return [p.strip().rstrip(".") for p in paragraph.split("-") if p.strip()]


def _png_size(path: Path) -> tuple[int | None, int | None]:
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
    except OSError:
        pass
    return None, None


def open_corpus(corpus_dir: str | Path | None = None, *, snapshot_id: str | None = None, paths: CorpusPaths | None = None) -> SqliteCorpusReader:
    """Open a completed snapshot read-only; the reader's ``snapshot_id`` attribute names it.

    ``corpus_dir`` is the corpus cache root (default ``data/cache/corpus``), whose CURRENT snapshot is opened unless
    ``snapshot_id`` pins one. A single snapshot directory (one containing ``manifest.json``) is also accepted.
    """
    if corpus_dir is not None:
        corpus_dir = Path(corpus_dir)
        if (corpus_dir / "manifest.json").is_file():
            base = paths or default_paths(cache_root=corpus_dir.parent.parent)
            return SqliteCorpusReader(corpus_dir, base)
        paths = default_paths(paths.package_root if paths else None, corpus_dir)
    paths = paths or default_paths()
    snapshot_id = snapshot_id or current_snapshot_id(paths)
    if not snapshot_id:
        raise CorpusNotReady(f"no corpus snapshot under {paths.cache_root}; run `python -m certivity.corpus build`")
    return SqliteCorpusReader(paths.snapshots / snapshot_id, paths)


def corpus_status(corpus_dir: str | Path | CorpusPaths | None = None) -> dict:
    """Readiness for the backend health route; never raises.

    ``corpus_dir`` is the corpus cache root (default ``data/cache/corpus``), like ``open_corpus``; a ``CorpusPaths``
    is also accepted.
    """
    paths = corpus_dir if isinstance(corpus_dir, CorpusPaths) else default_paths(cache_root=corpus_dir)
    try:
        reader = open_corpus(paths=paths)
    except CorpusNotReady as exc:
        return {"ready": False, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a health check reports a broken corpus instead of failing
        return {"ready": False, "reason": f"{type(exc).__name__}: {exc}"}
    try:
        return reader.status()
    finally:
        reader.close()
