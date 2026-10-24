"""Regulation corpus: acquisition, SQLite source store and the read-only CorpusReader.

Typical use by other components (never query the SQLite file directly)::

    from certivity.corpus import open_corpus
    corpus = open_corpus("data/cache/corpus")  # the CURRENT completed snapshot, read-only
    corpus.snapshot_id                         # identity to record with each run
    corpus.get_document_path(document_id)      # server-only PDF path for the /pdf route
    for doc in corpus.list_documents("base"):
        for section in corpus.list_sections(doc.document_id):
            bundle = corpus.read_section(section.section_id)

Bootstrap (backend startup or CLI)::

    from certivity.corpus import bootstrap
    status = bootstrap("data/participant-package", "data/cache/corpus")   # status["ready"], status["snapshot_id"]
"""

from __future__ import annotations

from pathlib import Path

from ._contracts import USING_PROVISIONAL_CONTRACTS
from .config import CorpusPaths, default_paths
from .reader import CorpusNotReady, SqliteCorpusReader, UnknownId, corpus_status, open_corpus


def bootstrap(package_dir: str | Path | None = None, corpus_dir: str | Path | None = None, *, fetch_missing: bool = False) -> dict:
    """Startup helper: build (or reuse) the snapshot for ``package_dir`` into ``corpus_dir`` and return its status.
    Downloads missing base PDFs first only when ``fetch_missing`` is set (network access)."""
    return ensure_corpus(default_paths(package_dir, corpus_dir), fetch_missing=fetch_missing)


def ensure_corpus(paths: CorpusPaths | None = None, *, fetch_missing: bool = False, roles: tuple[str, ...] = ("base",)) -> dict:
    """Build (or reuse) a snapshot for the files present; optionally download missing ones first. Returns status."""
    from .acquire import fetch
    from .registry import load_index
    from .store import build_snapshot

    paths = paths or default_paths()
    fetched = None
    if fetch_missing and any(not s.is_present for s in load_index(paths.package_root) if s.role in roles):
        fetched = [fetch(paths, role=role) for role in roles]
    report = build_snapshot(paths, roles)
    status = corpus_status(paths)
    status["build"] = report.to_json()
    if fetched is not None:
        status["fetch_failed"] = [f for r in fetched for f in r.failed]
    return status


__all__ = [
    "USING_PROVISIONAL_CONTRACTS",
    "CorpusNotReady",
    "CorpusPaths",
    "SqliteCorpusReader",
    "UnknownId",
    "bootstrap",
    "corpus_status",
    "default_paths",
    "ensure_corpus",
    "open_corpus",
]
