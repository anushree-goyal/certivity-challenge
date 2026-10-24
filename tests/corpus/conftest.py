from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from certivity.corpus import CorpusNotReady, default_paths, open_corpus  # noqa: E402


@pytest.fixture(scope="session")
def paths():
    return default_paths()


@pytest.fixture(scope="session")
def corpus(paths):
    """The real base corpus (CURRENT snapshot). Build it first: python -m certivity.corpus build"""
    try:
        reader = open_corpus(paths=paths)
    except CorpusNotReady as exc:
        pytest.skip(f"real corpus not built: {exc}")
    yield reader
    reader.close()


@pytest.fixture(scope="session")
def docs(corpus):
    return {d.regulation: d for d in corpus.list_documents("base")}


@pytest.fixture(scope="session")
def by_label(corpus):
    return lambda document_id, label: passage_by_label(corpus, document_id, label)


def passage_by_label(corpus, document_id, paragraph_label):
    for section in corpus.list_sections(document_id):
        for p in corpus.read_section(section.section_id, include_context=False).primary_passages:
            if p.paragraph_label == paragraph_label and "kind:paragraph" in p.parsing_flags and "continuation_after_table" not in p.parsing_flags:
                return p
    raise AssertionError(f"{paragraph_label} not found in {document_id}")
