"""Single import point for shared records: canonical contracts when present, provisional otherwise."""

from __future__ import annotations

try:  # canonical, owned by Anushree
    from certivity.contracts import (  # type: ignore[import-not-found]
        CorpusReader,
        Document,
        PageAsset,
        ReferenceRequest,
        ReferenceResolution,
        Section,
        SectionBundle,
        SourcePassage,
    )

    USING_PROVISIONAL_CONTRACTS = False
except ImportError:
    from ._provisional_contracts import (
        CorpusReader,
        Document,
        PageAsset,
        ReferenceRequest,
        ReferenceResolution,
        Section,
        SectionBundle,
        SourcePassage,
    )

    USING_PROVISIONAL_CONTRACTS = True

__all__ = [
    "USING_PROVISIONAL_CONTRACTS",
    "CorpusReader",
    "Document",
    "PageAsset",
    "ReferenceRequest",
    "ReferenceResolution",
    "Section",
    "SectionBundle",
    "SourcePassage",
]
