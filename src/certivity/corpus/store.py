"""SQLite corpus schema and the snapshot importer (the only writer).

A snapshot is a directory ``snapshots/<snapshot_id>/`` holding ``corpus.sqlite`` and ``manifest.json``. It is
built in a temporary directory and renamed into place when complete, so a reader never sees a partial snapshot,
and a published snapshot is never modified. ``CURRENT`` names the snapshot new runs should open.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .assemble import AssembledDocument, assemble
from .config import CorpusPaths
from .parse import PARSER_VERSION, ParsedDocument, parse_pdf
from .registry import DocumentSpec, file_sha256, index_sha256, load_index

SCHEMA_VERSION = "corpus-schema-2"

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE documents (
    document_id TEXT PRIMARY KEY, regulation TEXT NOT NULL, regulation_title TEXT NOT NULL, title TEXT NOT NULL,
    role TEXT NOT NULL, version_label TEXT NOT NULL, filename TEXT NOT NULL, checksum TEXT NOT NULL,
    size_bytes INTEGER NOT NULL, page_count INTEGER NOT NULL, package_relpath TEXT NOT NULL,
    footer_regulation TEXT, footer_version TEXT, parser_version TEXT NOT NULL, body_size REAL, left_margin REAL
);
CREATE TABLE pages (
    document_id TEXT NOT NULL, pdf_page_index INTEGER NOT NULL, printed_page INTEGER, printed_verified INTEGER NOT NULL,
    has_images INTEGER NOT NULL, has_tables INTEGER NOT NULL, drawing_count INTEGER NOT NULL, text_chars INTEGER NOT NULL,
    PRIMARY KEY (document_id, pdf_page_index)
);
CREATE TABLE sections (
    section_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, parent_section_id TEXT, namespace TEXT NOT NULL,
    raw_label TEXT, paragraph_label TEXT, depth INTEGER NOT NULL, heading TEXT NOT NULL, ord INTEGER NOT NULL,
    heading_passage_id TEXT, flags TEXT NOT NULL
);
CREATE INDEX sections_doc ON sections (document_id, ord);
CREATE TABLE passages (
    passage_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, section_id TEXT NOT NULL, namespace TEXT NOT NULL,
    raw_label TEXT, paragraph_label TEXT, kind TEXT NOT NULL, text TEXT NOT NULL, printed_start_page INTEGER,
    page_span TEXT NOT NULL, pdf_page_indices TEXT NOT NULL, flags TEXT NOT NULL, heading_ancestry TEXT NOT NULL,
    ord INTEGER NOT NULL, table_rows TEXT
);
CREATE INDEX passages_section ON passages (section_id, ord);
CREATE INDEX passages_label ON passages (document_id, namespace, raw_label);
CREATE TABLE definitions (document_id TEXT NOT NULL, term TEXT NOT NULL, term_norm TEXT NOT NULL, passage_id TEXT NOT NULL);
CREATE INDEX definitions_doc ON definitions (document_id);
CREATE TABLE reference_candidates (
    passage_id TEXT NOT NULL, document_id TEXT NOT NULL, raw TEXT NOT NULL, start INTEGER NOT NULL, "end" INTEGER NOT NULL,
    target_regulation_number TEXT, target_namespace TEXT, target_paragraph TEXT, explicit_this_regulation INTEGER NOT NULL
);
CREATE INDEX references_passage ON reference_candidates (passage_id);
CREATE TABLE import_diagnostics (
    document_id TEXT, pdf_page_index INTEGER, severity TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL
);
"""

FTS_SCHEMA = "CREATE VIRTUAL TABLE passages_fts USING fts5(passage_id UNINDEXED, document_id UNINDEXED, text);"


@dataclass
class BuildReport:
    snapshot_id: str
    reused: bool
    roles: list[str]
    documents_imported: list[str] = field(default_factory=list)
    not_downloaded: list[str] = field(default_factory=list)
    checksum_mismatch: list[str] = field(default_factory=list)
    parse_failed: dict[str, str] = field(default_factory=dict)
    parsed_from_cache: int = 0
    seconds: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


def _parse_cached(args: tuple[str, str, str]) -> tuple[str, dict, bool]:
    """Worker: (sha256, pdf path, cache dir) -> (sha, parsed json, from_cache)."""
    sha, pdf_path, cache_dir = args
    cache_file = Path(cache_dir) / f"{sha}.{PARSER_VERSION}.json.gz"
    if cache_file.is_file():
        with gzip.open(cache_file, "rt", encoding="utf-8") as handle:
            return sha, json.load(handle), True
    data = parse_pdf(pdf_path).to_json()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(f".tmp{os.getpid()}")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(data, handle)
    tmp.replace(cache_file)
    return sha, data, False


def _package_commit(package_root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(package_root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def code_fingerprint() -> str:
    """Hash of the code that shapes snapshot content, so any change to it yields a new snapshot id.
    (The parse cache is keyed by PARSER_VERSION instead: bump it when parse.py/structure.py output changes.)"""
    digest = hashlib.sha256()
    for name in ("parse.py", "structure.py", "assemble.py", "store.py", "registry.py"):
        digest.update((Path(__file__).parent / name).read_bytes())
    return digest.hexdigest()[:12]


def compute_snapshot_id(index_hash: str, roles: list[str], document_ids: list[str]) -> str:
    payload = json.dumps([SCHEMA_VERSION, PARSER_VERSION, code_fingerprint(), index_hash, sorted(roles), sorted(document_ids)])
    return "snap-" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def build_snapshot(paths: CorpusPaths, roles: tuple[str, ...] = ("base",), *, workers: int | None = None, make_current: bool = True) -> BuildReport:
    started = time.time()
    specs = [s for s in load_index(paths.package_root) if s.role in roles]
    diagnostics: list[tuple] = []
    usable: list[DocumentSpec] = []
    report = BuildReport(snapshot_id="", reused=False, roles=list(roles))
    for spec in specs:
        if not spec.is_present:
            report.not_downloaded.append(spec.document_id)
            diagnostics.append((spec.document_id, None, "error", "not_downloaded", f"{spec.local_path.name} is not in the package; run the fetch step"))
            continue
        if file_sha256(spec.local_path) != spec.sha256:
            report.checksum_mismatch.append(spec.document_id)
            diagnostics.append((spec.document_id, None, "error", "checksum_mismatch", "file on disk does not match index sha256; not imported"))
            continue
        usable.append(spec)

    index_hash = index_sha256(paths.package_root)
    # snapshot identity covers what was parseable; parse failures are discovered below and change the id
    parsed: dict[str, ParsedDocument] = {}
    jobs = [(s.sha256, str(s.local_path), str(paths.parsed)) for s in usable]
    with ProcessPoolExecutor(max_workers=workers or min(8, os.cpu_count() or 2)) as pool:
        futures = {s.sha256: pool.submit(_parse_cached, job) for s, job in zip(usable, jobs)}
        for spec in usable:
            try:
                _, data, from_cache = futures[spec.sha256].result()
                parsed[spec.document_id] = ParsedDocument.from_json(data)
                report.parsed_from_cache += int(from_cache)
            except Exception as exc:  # noqa: BLE001 - report per document, keep the rest
                report.parse_failed[spec.document_id] = f"{type(exc).__name__}: {exc}"
                diagnostics.append((spec.document_id, None, "error", "parse_failed", report.parse_failed[spec.document_id]))
    imported = [s for s in usable if s.document_id in parsed]
    report.documents_imported = [s.document_id for s in imported]
    snapshot_id = compute_snapshot_id(index_hash, list(roles), report.documents_imported)
    report.snapshot_id = snapshot_id

    final_dir = paths.snapshots / snapshot_id
    if (final_dir / "manifest.json").is_file() and json.loads((final_dir / "manifest.json").read_text()).get("complete"):
        report.reused = True
    else:
        tmp_dir = paths.snapshots / f".building-{snapshot_id}-{os.getpid()}"
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True)
        counts = _write_database(tmp_dir / "corpus.sqlite", snapshot_id, paths, imported, parsed, diagnostics, index_hash)
        manifest = {
            "snapshot_id": snapshot_id,
            "complete": True,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "schema_version": SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
            "code_fingerprint": code_fingerprint(),
            "index_sha256": index_hash,
            "package_root": str(paths.package_root),
            "package_commit": _package_commit(paths.package_root),
            "roles": list(roles),
            "documents": report.documents_imported,
            "not_downloaded": report.not_downloaded,
            "checksum_mismatch": report.checksum_mismatch,
            "parse_failed": report.parse_failed,
            "counts": counts,
        }
        (tmp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        if final_dir.exists():  # an incomplete leftover with the same id; never touch a complete one
            shutil.rmtree(final_dir)
        tmp_dir.rename(final_dir)
    report.counts = json.loads((final_dir / "manifest.json").read_text())["counts"]
    if make_current:
        set_current(paths, snapshot_id)
    report.seconds = round(time.time() - started, 1)
    return report


def set_current(paths: CorpusPaths, snapshot_id: str) -> None:
    pointer = paths.current_pointer
    pointer.parent.mkdir(parents=True, exist_ok=True)
    tmp = pointer.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(snapshot_id + "\n")
    tmp.replace(pointer)


def current_snapshot_id(paths: CorpusPaths) -> str | None:
    try:
        return paths.current_pointer.read_text().strip() or None
    except FileNotFoundError:
        return None


def prune_snapshots(paths: CorpusPaths, keep: int = 2) -> list[str]:
    """Delete old completed snapshots, never CURRENT. Manual only: a running job may still hold an old snapshot."""
    current = current_snapshot_id(paths)
    done = []
    for d in paths.snapshots.iterdir() if paths.snapshots.is_dir() else []:
        manifest = d / "manifest.json"
        if d.name.startswith(".building-") or not manifest.is_file():
            continue
        done.append((json.loads(manifest.read_text()).get("created_at", ""), d))
    done.sort(reverse=True)
    removed = []
    for _, d in done[keep:]:
        if d.name != current:
            shutil.rmtree(d)
            removed.append(d.name)
    return removed


def _write_database(db_path: Path, snapshot_id: str, paths: CorpusPaths, specs: list[DocumentSpec], parsed: dict[str, ParsedDocument], diagnostics: list[tuple], index_hash: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    try:
        conn.execute(FTS_SCHEMA)
        fts = True
    except sqlite3.OperationalError:
        fts = False
    meta = {"snapshot_id": snapshot_id, "schema_version": SCHEMA_VERSION, "parser_version": PARSER_VERSION, "index_sha256": index_hash, "fts": str(int(fts))}
    conn.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    for spec in specs:
        doc = parsed[spec.document_id]
        assembled: AssembledDocument = assemble(spec, doc)
        conn.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (spec.document_id, spec.regulation, spec.regulation_title, spec.version_title or f"{spec.regulation}, version {spec.version_label}", spec.role, spec.version_label,
             spec.filename, spec.sha256, spec.size_bytes, doc.page_count, str(spec.local_path.relative_to(paths.package_root)),
             doc.footer_regulation, doc.footer_version, doc.parser_version, doc.body_size, doc.left_margin),
        )
        if doc.footer_regulation and (doc.footer_regulation != spec.regulation or doc.footer_version != spec.version_label):
            diagnostics.append((spec.document_id, None, "error", "footer_identity_mismatch", f"footer names {doc.footer_regulation} {doc.footer_version}, index says {spec.regulation} {spec.version_label}"))
        conn.executemany(
            "INSERT INTO pages VALUES (?,?,?,?,?,?,?,?)",
            [(spec.document_id, p.pdf_page_index, p.printed_page, int(p.printed_label_verified), int(p.has_images), int(p.has_tables), p.drawing_count, p.text_chars) for p in doc.pages],
        )
        conn.executemany(
            "INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(s.section_id, s.document_id, s.parent_section_id, s.namespace, s.raw_label, s.paragraph_label, s.depth, s.heading, s.order, s.heading_passage_id, json.dumps(s.flags)) for s in assembled.sections],
        )
        conn.executemany(
            "INSERT INTO passages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(p.passage_id, p.document_id, p.section_id, p.namespace, p.raw_label, p.paragraph_label, p.kind, p.text, p.printed_start_page,
              json.dumps(p.page_span), json.dumps(p.pdf_page_indices), json.dumps(p.flags), json.dumps(p.heading_ancestry), p.order,
              json.dumps(p.table_rows) if p.table_rows is not None else None) for p in assembled.passages],
        )
        if fts:
            conn.executemany("INSERT INTO passages_fts VALUES (?,?,?)", [(p.passage_id, p.document_id, p.text) for p in assembled.passages])
        conn.executemany("INSERT INTO definitions VALUES (?,?,?,?)", [(d.document_id, d.term, d.term_norm, d.passage_id) for d in assembled.definitions])
        conn.executemany(
            "INSERT INTO reference_candidates VALUES (?,?,?,?,?,?,?,?,?)",
            [(r.passage_id, r.document_id, r.raw, r.start, r.end, r.target_regulation_number, r.target_namespace, r.target_paragraph, int(r.explicit_this_regulation)) for r in assembled.references],
        )
        for d in doc.diagnostics:
            diagnostics.append((spec.document_id, d.pdf_page_index, d.severity, d.code, d.message))
    conn.executemany("INSERT INTO import_diagnostics VALUES (?,?,?,?,?)", diagnostics)
    conn.commit()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("documents", "pages", "sections", "passages", "definitions", "reference_candidates", "import_diagnostics")}
    conn.execute("VACUUM")
    conn.close()
    return counts
