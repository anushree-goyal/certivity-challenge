"""PROVISIONAL stand-ins for the shared records in ``certivity.contracts``.

Anushree owns the canonical definitions. This file exists only so the corpus
component can be built and tested before ``src/certivity/contracts.py`` is
published. Field names follow the master-plan record table exactly; any field
beyond that table is listed in docs/corpus/contract-proposal.md as a requested
addition. ``certivity.corpus._contracts`` prefers the canonical module whenever
it is importable, so delete this file once the real contracts land.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

ReferenceStatus = Literal["resolved", "ambiguous", "missing", "outside_corpus"]


@dataclass(frozen=True)
class Document:
    document_id: str
    regulation: str
    title: str
    role: str
    version_label: str
    filename: str
    checksum: str
    snapshot_id: str
    page_count: int


@dataclass(frozen=True)
class SourcePassage:
    passage_id: str
    document_id: str
    section_id: str
    text: str
    paragraph_label: str | None
    heading_ancestry: list[str]
    printed_start_page: int | None
    page_span: list[int]
    pdf_page_indices: list[int]
    parsing_flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Section:
    section_id: str
    document_id: str
    parent_section_id: str | None
    heading: str
    namespace: str
    paragraph_label: str | None
    primary_passage_ids: list[str]
    parsing_flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SectionBundle:
    section: Section
    primary_passages: list[SourcePassage]
    context_passages: list[SourcePassage]
    visual_pdf_page_indices: list[int]


@dataclass(frozen=True)
class ReferenceRequest:
    originating_passage_id: str
    raw_reference: str
    target_regulation: str | None = None
    target_annex: str | None = None
    target_paragraph: str | None = None


@dataclass(frozen=True)
class ReferenceResolution:
    status: ReferenceStatus
    candidate_passage_ids: list[str]
    explanation: str


@dataclass(frozen=True)
class PageAsset:
    document_id: str
    pdf_page_index: int
    printed_page_label: str | None
    media_type: str
    asset_ref: str


class CorpusReader(Protocol):
    def list_documents(self, role: str = "base") -> list[Document]: ...
    def read_scope(self, document_id: str) -> list[SourcePassage]: ...
    def list_sections(self, document_id: str) -> list[Section]: ...
    def read_section(self, section_id: str, include_context: bool = True) -> SectionBundle: ...
    def get_passages(self, passage_ids: list[str]) -> list[SourcePassage]: ...
    def resolve_reference(self, request: ReferenceRequest) -> ReferenceResolution: ...
    def get_page_asset(self, document_id: str, pdf_page_index: int) -> PageAsset: ...
