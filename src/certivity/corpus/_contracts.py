"""Single import point for the shared records the corpus produces (canonical: ``certivity.contracts``, Anushree)."""

from __future__ import annotations

from certivity.contracts import (
    CorpusReader,
    CorpusSearch,
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

USING_PROVISIONAL_CONTRACTS = False  # kept for callers that checked it; the provisional module has been removed

__all__ = [
    "USING_PROVISIONAL_CONTRACTS",
    "CorpusReader",
    "CorpusSearch",
    "Document",
    "DocumentRole",
    "PageAsset",
    "ReferenceRequest",
    "ReferenceResolution",
    "ReferenceStatus",
    "Section",
    "SectionBundle",
    "SourcePassage",
]
