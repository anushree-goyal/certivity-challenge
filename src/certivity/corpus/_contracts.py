"""Single import point for shared records: canonical contracts when present, provisional otherwise."""

from __future__ import annotations

_NAMES = (
    "CorpusReader",
    "Document",
    "DocumentRole",
    "PageAsset",
    "PageRef",
    "ReferenceRequest",
    "ReferenceResolution",
    "ReferenceStatus",
    "Section",
    "SectionBundle",
    "SourcePassage",
)

try:  # canonical, owned by Anushree
    from certivity.contracts import (  # type: ignore[import-not-found]
        CorpusReader,
        Document,
        DocumentRole,
        PageAsset,
        PageRef,
        ReferenceRequest,
        ReferenceResolution,
        ReferenceStatus,
        Section,
        SectionBundle,
        SourcePassage,
    )

    USING_PROVISIONAL_CONTRACTS = False
except ImportError:
    from ._provisional_contracts import (
        CorpusReader,
        Document,
        DocumentRole,
        PageAsset,
        PageRef,
        ReferenceRequest,
        ReferenceResolution,
        ReferenceStatus,
        Section,
        SectionBundle,
        SourcePassage,
    )

    USING_PROVISIONAL_CONTRACTS = True

__all__ = ["USING_PROVISIONAL_CONTRACTS", *_NAMES]
