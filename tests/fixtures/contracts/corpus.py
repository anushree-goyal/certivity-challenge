"""SYNTHETIC in-memory CorpusReader (owner: Anushree).

**This is a fixture, not data.** Every document here is invented, is named
``SYNTHETIC ...`` and carries ``snapshot_id="synthetic-fixture-snapshot"``. No
output derived from it may be presented as an extraction result, and it is not
a substitute for Teammate 2's real importer.

It exists so that the agent, quality and application components can be built
and tested before PDF ingestion is finished. It deliberately reproduces the
awkward parts of real source:

* the same paragraph label ``2.1`` in the main body and in Annex 1, so code
  that keys on a bare label breaks here rather than in the demo;
* a passage that spans two printed pages;
* a table cell that is meaningless without its headers and footnote;
* a cross reference that resolves, one that is ambiguous, and one that points
  outside the corpus;
* printed page labels that differ from zero-based physical page indices.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from certivity.contracts import (
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
    document_id_for,
    namespace_for,
    passage_id_for,
    section_id_for,
)

SNAPSHOT_ID = "synthetic-fixture-snapshot"

REG_A = "SYNTHETIC REG A"
REG_B = "SYNTHETIC REG B"

DOC_A = document_id_for(REG_A, "00 series", "aaaa1111bbbb2222")
DOC_B = document_id_for(REG_B, "01 series", "cccc3333dddd4444")

MAIN = namespace_for("main")
ANNEX_1 = namespace_for("annex", "1")


def _sec(document_id: str, namespace: str, number: str) -> str:
    return section_id_for(document_id, namespace, number)


# Section ids used by the tests.
A_SCOPE = _sec(DOC_A, MAIN, "1")
A_DEFS = _sec(DOC_A, MAIN, "2")
A_SPECS = _sec(DOC_A, MAIN, "3")
A_ANNEX_TEST = _sec(DOC_A, ANNEX_1, "2")
A_CONTENTS = _sec(DOC_A, MAIN, "0")
B_SCOPE = _sec(DOC_B, MAIN, "1")


def _passage(
    section_id: str,
    ordinal: int,
    text: str,
    *,
    document_id: str,
    namespace: str,
    paragraph_label: str | None,
    headings: Sequence[str],
    printed: str,
    printed_span: Sequence[str] = (),
    pdf_pages: Sequence[int] = (),
    is_table: bool = False,
    table_context: str | None = None,
    flags: Sequence[str] = (),
) -> SourcePassage:
    return SourcePassage(
        passage_id=passage_id_for(section_id, ordinal),
        document_id=document_id,
        section_id=section_id,
        text=text,
        paragraph_label=paragraph_label,
        heading_ancestry=tuple(headings),
        printed_start_page=printed,
        printed_page_span=tuple(printed_span or (printed,)),
        pdf_page_indices=tuple(pdf_pages),
        namespace=namespace,
        is_table=is_table,
        table_context=table_context,
        parsing_flags=tuple(flags),
    )


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #

DOCUMENTS: tuple[Document, ...] = (
    Document(
        document_id=DOC_A,
        regulation=REG_A,
        title="Synthetic uniform provisions for signalling devices (FIXTURE)",
        role=DocumentRole.BASE,
        version_label="00 series",
        filename="synthetic-reg-a-00.pdf",
        checksum="aaaa1111bbbb2222",
        snapshot_id=SNAPSHOT_ID,
        page_count=12,
        index_entry={"regulation": REG_A, "scope_hint": "signalling devices", "fixture": True},
    ),
    Document(
        document_id=DOC_B,
        regulation=REG_B,
        title="Synthetic uniform provisions for pressure vessels (FIXTURE)",
        role=DocumentRole.BASE,
        version_label="01 series",
        filename="synthetic-reg-b-01.pdf",
        checksum="cccc3333dddd4444",
        snapshot_id=SNAPSHOT_ID,
        page_count=4,
        index_entry={"regulation": REG_B, "scope_hint": "pressure vessels", "fixture": True},
    ),
)


# --------------------------------------------------------------------------- #
# Passages
# --------------------------------------------------------------------------- #

A_SCOPE_P1 = _passage(
    A_SCOPE,
    1,
    "This Regulation applies to synthetic signalling devices of category SX "
    "intended for installation on power-driven vehicles of category M1.",
    document_id=DOC_A,
    namespace=MAIN,
    paragraph_label="1.1",
    headings=("1. SCOPE",),
    printed="3",
    pdf_pages=(2,),
)
A_DEF_P1 = _passage(
    A_DEFS,
    1,
    '"Signalling device" means a device intended to signal the presence of a '
    "vehicle to other road users.",
    document_id=DOC_A,
    namespace=MAIN,
    paragraph_label="2.1",
    headings=("2. DEFINITIONS",),
    printed="4",
    pdf_pages=(3,),
)
A_DEF_P2 = _passage(
    A_DEFS,
    2,
    '"Reference axis" means the axis marked on the device, which shall not '
    "deviate by more than 1,5 degrees from the geometric centre line.",
    document_id=DOC_A,
    namespace=MAIN,
    paragraph_label="2.2",
    headings=("2. DEFINITIONS",),
    printed="4",
    pdf_pages=(3,),
)
A_SPEC_COLOUR = _passage(
    A_SPECS,
    1,
    "The light emitted by the device shall be red.",
    document_id=DOC_A,
    namespace=MAIN,
    paragraph_label="3.1",
    headings=("3. SPECIFICATIONS",),
    printed="5",
    pdf_pages=(4,),
)
A_SPEC_INTENSITY = _passage(
    A_SPECS,
    2,
    "The luminous intensity shall be between 4 cd and 12 cd when measured along "
    "the reference axis, except for devices bearing the marking \"R2\", for which "
    "the luminous intensity shall be between 2 cd and 6 cd.",
    document_id=DOC_A,
    namespace=MAIN,
    paragraph_label="3.2",
    headings=("3. SPECIFICATIONS",),
    printed="5",
    printed_span=("5", "6"),
    pdf_pages=(4, 5),
    flags=("spans_pages",),
)
A_SPEC_REFERENCE = _passage(
    A_SPECS,
    3,
    "The device shall comply with the test procedure set out in Annex 1, "
    "paragraph 2.1, and with the provisions of SYNTHETIC REG Z.",
    document_id=DOC_A,
    namespace=MAIN,
    paragraph_label="3.3",
    headings=("3. SPECIFICATIONS",),
    printed="6",
    pdf_pages=(5,),
)
A_ANNEX_P1 = _passage(
    A_ANNEX_TEST,
    1,
    "The measurement shall be carried out at an ambient temperature of 25 °C "
    "after 30 minutes of operation.",
    document_id=DOC_A,
    namespace=ANNEX_1,
    paragraph_label="2.1",
    headings=("ANNEX 1", "2. TEST PROCEDURE"),
    printed="9",
    pdf_pages=(8,),
)
A_ANNEX_TABLE = _passage(
    A_ANNEX_TEST,
    2,
    "SX-1 | 4 | 12",
    document_id=DOC_A,
    namespace=ANNEX_1,
    paragraph_label="2.2",
    headings=("ANNEX 1", "2. TEST PROCEDURE"),
    printed="10",
    pdf_pages=(9,),
    is_table=True,
    table_context=(
        "Table 1 - Minimum and maximum luminous intensity in cd. "
        "Columns: device category | minimum | maximum. "
        "Footnote (a): values apply at 25 °C."
    ),
)
B_SCOPE_P1 = _passage(
    B_SCOPE,
    1,
    "This Regulation applies to welded pressure vessels for the storage of "
    "compressed air on vehicles of category N3.",
    document_id=DOC_B,
    namespace=MAIN,
    paragraph_label="1.1",
    headings=("1. SCOPE",),
    printed="2",
    pdf_pages=(1,),
)

PASSAGES: tuple[SourcePassage, ...] = (
    A_SCOPE_P1,
    A_DEF_P1,
    A_DEF_P2,
    A_SPEC_COLOUR,
    A_SPEC_INTENSITY,
    A_SPEC_REFERENCE,
    A_ANNEX_P1,
    A_ANNEX_TABLE,
    B_SCOPE_P1,
)


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

SECTIONS: tuple[Section, ...] = (
    Section(
        section_id=A_CONTENTS,
        document_id=DOC_A,
        parent_section_id=None,
        heading="Table of contents",
        namespace=MAIN,
        number="0",
        passage_ids=(),
        ordinal=0,
    ),
    Section(
        section_id=A_SCOPE,
        document_id=DOC_A,
        parent_section_id=None,
        heading="1. SCOPE",
        namespace=MAIN,
        number="1",
        passage_ids=(A_SCOPE_P1.passage_id,),
        ordinal=1,
    ),
    Section(
        section_id=A_DEFS,
        document_id=DOC_A,
        parent_section_id=None,
        heading="2. DEFINITIONS",
        namespace=MAIN,
        number="2",
        passage_ids=(A_DEF_P1.passage_id, A_DEF_P2.passage_id),
        ordinal=2,
    ),
    Section(
        section_id=A_SPECS,
        document_id=DOC_A,
        parent_section_id=None,
        heading="3. SPECIFICATIONS",
        namespace=MAIN,
        number="3",
        passage_ids=(
            A_SPEC_COLOUR.passage_id,
            A_SPEC_INTENSITY.passage_id,
            A_SPEC_REFERENCE.passage_id,
        ),
        ordinal=3,
    ),
    Section(
        section_id=A_ANNEX_TEST,
        document_id=DOC_A,
        parent_section_id=None,
        heading="2. TEST PROCEDURE",
        namespace=ANNEX_1,
        number="2",
        passage_ids=(A_ANNEX_P1.passage_id, A_ANNEX_TABLE.passage_id),
        ordinal=4,
    ),
    Section(
        section_id=B_SCOPE,
        document_id=DOC_B,
        parent_section_id=None,
        heading="1. SCOPE",
        namespace=MAIN,
        number="1",
        passage_ids=(B_SCOPE_P1.passage_id,),
        ordinal=1,
    ),
)


class InMemoryCorpusReader:
    """SYNTHETIC ``CorpusReader``. Read-only; it has no writer at all.

    Reference resolution mirrors the contract Teammate 2 implements: an
    annex-qualified target resolves inside the right namespace, a bare
    paragraph label that exists in two namespaces comes back ``AMBIGUOUS``
    rather than silently picking one, and a regulation that is not in the
    snapshot comes back ``OUTSIDE_CORPUS``.
    """

    is_synthetic_fixture = True

    def __init__(
        self,
        documents: Iterable[Document] = DOCUMENTS,
        sections: Iterable[Section] = SECTIONS,
        passages: Iterable[SourcePassage] = PASSAGES,
        snapshot_id: str = SNAPSHOT_ID,
    ) -> None:
        self._documents = list(documents)
        self._sections = list(sections)
        self._passages = {p.passage_id: p for p in passages}
        self._snapshot_id = snapshot_id

    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    def list_documents(self, role: DocumentRole | str = DocumentRole.BASE) -> list[Document]:
        wanted = DocumentRole(role) if isinstance(role, str) else role
        return [d for d in self._documents if d.role is wanted]

    def read_scope(self, document_id: str) -> list[SourcePassage]:
        """Scope and definition passages, which is what a scope decision reads."""
        return [
            p
            for p in self._passages.values()
            if p.document_id == document_id
            and p.section_id in {_sec(document_id, MAIN, "1"), _sec(document_id, MAIN, "2")}
        ]

    def list_sections(self, document_id: str) -> list[Section]:
        return [s for s in self._sections if s.document_id == document_id]

    def read_section(self, section_id: str, include_context: bool = True) -> SectionBundle:
        section = next((s for s in self._sections if s.section_id == section_id), None)
        if section is None:
            raise KeyError(f"unknown section: {section_id}")
        primary = tuple(
            self._passages[pid] for pid in section.passage_ids if pid in self._passages
        )
        context: tuple[SourcePassage, ...] = ()
        if include_context and section.section_id == A_SPECS:
            # Scope and the reference-axis definition are the inherited context a
            # correct reading of section 3 needs.
            context = (A_SCOPE_P1, A_DEF_P2)
        page_refs = tuple(
            PageRef(
                document_id=p.document_id,
                pdf_page_index=p.pdf_page_indices[0],
                printed_page=p.printed_start_page,
                reason="table",
            )
            for p in primary
            if p.is_table and p.pdf_page_indices
        )
        return SectionBundle(
            section=section,
            primary_passages=primary,
            context_passages=context,
            page_refs=page_refs,
            notes=("synthetic fixture bundle",),
        )

    def get_passages(self, passage_ids: Sequence[str]) -> list[SourcePassage]:
        return [self._passages[pid] for pid in passage_ids if pid in self._passages]

    def resolve_reference(self, request: ReferenceRequest) -> ReferenceResolution:
        regulation = (request.target_regulation or "").strip().upper()
        known = {d.regulation.upper() for d in self._documents}
        if regulation and regulation not in known:
            return ReferenceResolution(
                request=request,
                status=ReferenceStatus.OUTSIDE_CORPUS,
                explanation=f"{request.target_regulation} is not in this snapshot",
            )

        paragraph = (request.target_paragraph or "").strip()
        if not paragraph:
            return ReferenceResolution(
                request=request,
                status=ReferenceStatus.MISSING,
                explanation="the reference names no paragraph to resolve",
            )

        namespace = request.target_namespace
        matches = [
            p
            for p in self._passages.values()
            if p.paragraph_label == paragraph
            and (namespace is None or p.namespace == namespace)
        ]
        if not matches:
            return ReferenceResolution(
                request=request,
                status=ReferenceStatus.MISSING,
                explanation=f"paragraph {paragraph} was not found in namespace {namespace}",
            )
        namespaces = {p.namespace for p in matches}
        if len(namespaces) > 1:
            return ReferenceResolution(
                request=request,
                status=ReferenceStatus.AMBIGUOUS,
                explanation=(
                    f"paragraph {paragraph} exists in {sorted(namespaces)}; the reference "
                    "does not say which"
                ),
                candidate_passage_ids=tuple(p.passage_id for p in matches),
            )
        resolved = matches[0]
        return ReferenceResolution(
            request=request,
            status=ReferenceStatus.RESOLVED,
            explanation=f"resolved in namespace {resolved.namespace}",
            candidate_passage_ids=tuple(p.passage_id for p in matches),
            resolved_document_id=resolved.document_id,
            resolved_section_id=resolved.section_id,
        )

    def get_page_asset(self, document_id: str, pdf_page_index: int) -> PageAsset:
        document = next((d for d in self._documents if d.document_id == document_id), None)
        if document is None:
            raise KeyError(f"unknown document: {document_id}")
        if not 0 <= pdf_page_index < document.page_count:
            raise IndexError(
                f"page index {pdf_page_index} is outside {document.filename} "
                f"({document.page_count} pages)"
            )
        printed = next(
            (
                p.printed_start_page
                for p in self._passages.values()
                if p.document_id == document_id and pdf_page_index in p.pdf_page_indices
            ),
            None,
        )
        return PageAsset(
            document_id=document_id,
            pdf_page_index=pdf_page_index,
            printed_page=printed,
            media_type="image/png",
            asset_ref=f"synthetic://{document_id}/page-{pdf_page_index}.png",
        )
