"""PROVISIONAL stand-ins for the source records in ``certivity.contracts``.

Anushree owns the canonical definitions (contracts 1.1.0 on ``feature/agent``). This file mirrors their field
names, types and defaults for the records the corpus produces, so the corpus runs and is tested before that module
reaches this branch. ``certivity.corpus._contracts`` prefers the canonical module whenever it is importable; delete
this file once ``src/certivity/contracts.py`` is merged.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


class DocumentRole(str, enum.Enum):
    BASE = "base"
    LATEST = "latest"


class ReferenceStatus(str, enum.Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    OUTSIDE_CORPUS = "outside_corpus"


@dataclass(frozen=True)
class Document:
    document_id: str
    regulation: str
    title: str
    role: DocumentRole
    version_label: str
    filename: str
    checksum: str
    snapshot_id: str
    page_count: int
    index_entry: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourcePassage:
    passage_id: str
    document_id: str
    section_id: str
    text: str
    paragraph_label: Optional[str]
    heading_ancestry: tuple[str, ...]
    printed_start_page: Optional[str]
    printed_page_span: tuple[str, ...] = ()
    pdf_page_indices: tuple[int, ...] = ()
    namespace: str = "main"
    parsing_flags: tuple[str, ...] = ()
    is_table: bool = False
    table_context: Optional[str] = None


@dataclass(frozen=True)
class Section:
    section_id: str
    document_id: str
    parent_section_id: Optional[str]
    heading: str
    namespace: str
    number: Optional[str] = None
    passage_ids: tuple[str, ...] = ()
    depth: int = 0
    ordinal: int = 0
    parsing_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageRef:
    document_id: str
    pdf_page_index: int
    printed_page: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class SectionBundle:
    section: Section
    primary_passages: tuple[SourcePassage, ...]
    context_passages: tuple[SourcePassage, ...] = ()
    page_refs: tuple[PageRef, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageAsset:
    document_id: str
    pdf_page_index: int
    printed_page: Optional[str]
    media_type: str
    asset_ref: str
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass(frozen=True)
class ReferenceRequest:
    origin_passage_id: str
    raw_reference: str
    target_regulation: Optional[str] = None
    target_namespace: Optional[str] = None
    target_paragraph: Optional[str] = None
    origin_document_id: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class ReferenceResolution:
    request: ReferenceRequest
    status: ReferenceStatus
    explanation: str
    candidate_passage_ids: tuple[str, ...] = ()
    resolved_document_id: Optional[str] = None
    resolved_section_id: Optional[str] = None

    @property
    def is_usable(self) -> bool:
        return self.status is ReferenceStatus.RESOLVED and bool(self.candidate_passage_ids)


@runtime_checkable
class CorpusReader(Protocol):
    @property
    def snapshot_id(self) -> str: ...
    def list_documents(self, role: DocumentRole | str = DocumentRole.BASE) -> list[Document]: ...
    def read_scope(self, document_id: str) -> list[SourcePassage]: ...
    def list_sections(self, document_id: str) -> list[Section]: ...
    def read_section(self, section_id: str, include_context: bool = True) -> SectionBundle: ...
    def get_passages(self, passage_ids) -> list[SourcePassage]: ...
    def resolve_reference(self, request: ReferenceRequest) -> ReferenceResolution: ...
    def get_page_asset(self, document_id: str, pdf_page_index: int) -> PageAsset: ...
