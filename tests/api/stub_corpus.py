"""Stand-in for the corpus entry points (open_corpus, corpus_status, bootstrap) with controllable contents.

Tests set DOCUMENTS (the active snapshot's base documents) and BOOTSTRAP_GATE/BOOTSTRAP_CALLS to observe
re-imports. Mirrors the public CorpusReader shape only; it never touches the real SQLite store.
"""

from __future__ import annotations

import threading
from typing import Any

from certivity.api import fixture_mode

DOCUMENTS: list[dict[str, Any]] = []
SNAPSHOT = ["stub-snap-1"]
BOOTSTRAP_CALLS: list[tuple[str, str]] = []
BOOTSTRAP_GATE = threading.Event()
BOOTSTRAP_GATE.set()


class StubCorpus(fixture_mode.FixtureCorpus):
    def __init__(self, snapshot_id=None):
        self.snapshot_id = snapshot_id or SNAPSHOT[0]

    def list_documents(self, role="base"):
        return [d for d in DOCUMENTS if d.get("role", "base") == role]


def open_corpus(corpus_dir, snapshot_id=None):
    return StubCorpus(snapshot_id)


def corpus_status(corpus_dir=None):
    return {"ready": True, "snapshot_id": SNAPSHOT[0], "documents": len(DOCUMENTS)}


def bootstrap(package_dir, corpus_dir, **kwargs):
    BOOTSTRAP_CALLS.append((str(package_dir), str(corpus_dir)))
    BOOTSTRAP_GATE.wait(10)
    SNAPSHOT[0] = f"stub-snap-{len(BOOTSTRAP_CALLS) + 1}"
    return {"ready": True, "snapshot_id": SNAPSHOT[0]}


def reset():
    DOCUMENTS.clear()
    SNAPSHOT[0] = "stub-snap-1"
    BOOTSTRAP_CALLS.clear()
    BOOTSTRAP_GATE.set()
