"""Canonical shared records, identifiers and interfaces for the Certivity application.

Ownership: Anushree (section 1 of certivity-main-goal-build-plan.md). Every other
component imports from this module instead of defining its own field names.
Propose changes to this owner; do not fork a local copy.

Hard rules for this module:

* Standard library only. No pydantic, no FastAPI, no sqlite, no provider SDK.
* It imports none of the feature implementations, so nothing here can create a
  circular import.
* Nullable fields are explicit. ``None`` means "not established", which is not
  the same as an empty string or an empty list.

Identity conventions that the whole team depends on:

* ``pdf_page_index`` is always a **zero-based physical page index** into the
  supplied PDF file.
* ``printed_*_page`` is always the **page label printed on the page** of the
  supplied Certivity PDF, as a string (labels are not always integers). The
  official submission's ``source.page`` is a printed page.
* A paragraph label alone never identifies a passage. ``2.1`` in Annex 4 and
  ``2.1`` in the main body are different sections, so section and passage IDs
  carry a document ID and an annex namespace.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import mimetypes
import re
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Union, runtime_checkable

CONTRACTS_VERSION = "1.4.0"

# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class DocumentRole(str, enum.Enum):
    """Which version of a regulation a document file carries."""

    BASE = "base"
    LATEST = "latest"


class ApplicabilityStatus(str, enum.Enum):
    APPLICABLE = "applicable"
    UNCERTAIN = "uncertain"
    EXCLUDED = "excluded"


class ReferenceStatus(str, enum.Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    OUTSIDE_CORPUS = "outside_corpus"


class CoverageStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    EXCLUDED = "excluded"
    FAILED = "failed"


class ObligationKind(str, enum.Enum):
    """What kind of statement a draft requirement carries.

    ``MANDATORY`` is a constraint that must hold. ``PERMITTED_ALTERNATIVE`` is an
    option the source allows. ``EXCLUSION`` records that the source removes a
    case from scope. Collapsing these three loses the meaning of the source.
    """

    MANDATORY = "mandatory"
    PERMITTED_ALTERNATIVE = "permitted_alternative"
    EXCLUSION = "exclusion"
    DEFINITION_RULE = "definition_rule"


class FindingCategory(str, enum.Enum):
    UNSUPPORTED_BY_SOURCE = "unsupported_by_source"
    MISSING_CONDITION = "missing_condition"
    NUMERIC_MISMATCH = "numeric_mismatch"
    WRONG_SOURCE_VERSION = "wrong_source_version"
    UNRESOLVED_SOURCE = "unresolved_source"
    DUPLICATE = "duplicate"
    NOT_ATOMIC = "not_atomic"
    OUT_OF_SCOPE = "out_of_scope"
    CONTEXT_REQUESTED = "context_requested"
    TOO_LONG = "too_long"
    OTHER = "other"


class FindingSeverity(str, enum.Enum):
    BLOCKING = "blocking"
    REVISION_REQUESTED = "revision_requested"
    INFORMATIONAL = "informational"


class RunState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class TargetKind(str, enum.Enum):
    """What a coverage item or review finding points at."""

    DOCUMENT = "document"
    SECTION = "section"
    DRAFT = "draft"
    PASSAGE = "passage"
    RUN = "run"


# --------------------------------------------------------------------------- #
# Identifier helpers
#
# These exist so that four components produce byte-identical IDs for the same
# object. Teammate 2 generates them during import; everyone else only compares
# and passes them through.
# --------------------------------------------------------------------------- #

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase ASCII slug used inside identifiers."""
    return _SLUG_RE.sub("-", value.strip().lower()).strip("-")


def document_id_for(regulation: str, version_label: str, checksum: str) -> str:
    """``un-r148:03-series:sha256-9f2c1a4b7d0e``

    The checksum segment is what makes two files of the same regulation version
    distinguishable, which the plan requires for source identity.
    """
    return f"{slugify(regulation)}:{slugify(version_label)}:sha256-{checksum[:12]}"


def namespace_for(*segments: str | tuple[str, Optional[str]]) -> str:
    """Build a canonical namespace from one or more structural segments.

    Grammar, settled against the structures that actually occur in the supplied
    PDFs: segments joined by ``/``, each segment either a bare ``kind`` or
    ``kind:label``, lowercased.

        namespace_for("main")                            -> "main"
        namespace_for(("annex", "4"))                    -> "annex:4"
        namespace_for(("annex", "6"), ("part", "6"))     -> "annex:6/part:6"
        namespace_for(("annex", "4"), ("appendix", "1")) -> "annex:4/appendix:1"
        namespace_for(("annex", "5"), "appendix")        -> "annex:5/appendix"
        namespace_for(("appendix", "2"))                 -> "appendix:2"
        namespace_for("introduction")                    -> "introduction"
        namespace_for(("part", "I"))                     -> "part:i"

    An unnumbered division is a bare kind, which is why
    ``annex:5/appendix`` and ``appendix:2`` are both expressible.

    This is the **machine** form, used for matching, ID construction and
    reference resolution. The human citation string belongs in
    ``paragraph_label``; do not reconstruct one from a namespace.
    """
    parts: list[str] = []
    for segment in segments:
        if isinstance(segment, tuple):
            kind, label = segment
        else:
            kind, label = segment, None
        kind = slugify(kind)
        if label is None or str(label).strip() == "":
            parts.append(kind)
        else:
            parts.append(f"{kind}:{slugify(str(label))}")
    return "/".join(parts) if parts else "main"


def parse_namespace(namespace: str) -> tuple[tuple[str, Optional[str]], ...]:
    """Inverse of ``namespace_for``, for display and for structural comparison."""
    out: list[tuple[str, Optional[str]]] = []
    for part in namespace.split("/"):
        if not part:
            continue
        kind, _, label = part.partition(":")
        out.append((kind, label or None))
    return tuple(out)


def section_id_for(document_id: str, namespace: str, number: str) -> str:
    """``<document_id>#annex:4/2.1`` - never bare ``2.1``."""
    return f"{document_id}#{namespace}/{number.strip()}"


def passage_id_for(
    document_id: str, namespace: str, label: str, ordinal: int = 1
) -> str:
    """``<document_id>#annex:4/2.1.3@001`` - keyed on the paragraph label.

    Deliberately **not** derived from a section ID. A passage ID keyed on its
    section would change whenever sectioning changed, which would invalidate
    every cached model call, every saved run and every citation in an exported
    submission. Keyed on the label, an ID survives re-sectioning as long as the
    source's own label does.

    ``ordinal`` disambiguates within one label - a continuation paragraph, a
    table row, or an unlabelled passage attributed to its nearest preceding
    label. It is 1 for the overwhelmingly common single-passage case, and the
    ``@NNN`` suffix is always present so a passage ID can never collide with the
    section ID of the same label.
    """
    return f"{document_id}#{namespace}/{str(label).strip()}@{ordinal:03d}"


def content_hash(*parts: Any) -> str:
    """Stable hash over the parts, used for input hashes and model-call cache keys."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(repr(part).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Run input and configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DrawingRef:
    """A drawing supplied with the case, addressed the way the backend stores it."""

    drawing_id: str
    media_type: str
    asset_ref: str
    """Backend-local reference (path or run-store key). Never served raw to a browser."""
    caption: Optional[str] = None


@dataclass(frozen=True)
class RunConfig:
    """Per-run limits. Teammate 4 sets these; the agent component obeys them."""

    max_documents: Optional[int] = None
    """None means "no cap": inspect every applicable document."""
    max_sections_per_document: Optional[int] = None
    max_model_calls: Optional[int] = 400
    max_reference_depth: int = 2
    max_review_rounds: int = 1
    passages_per_batch: int = 6
    request_timeout_s: float = 120.0
    max_retries: int = 2
    use_cache: bool = True
    include_uncertain_documents: bool = True
    """Keep ApplicabilityStatus.UNCERTAIN candidates in the inspection plan."""
    model_overrides: dict[str, str] = field(default_factory=dict)
    """task name -> model id, for tasks that need a different model."""


@dataclass(frozen=True)
class RunInput:
    run_id: str
    team_id: str
    case_id: str
    corpus_snapshot_id: str
    input_hash: str
    specification_text: Optional[str] = None
    drawings: tuple[DrawingRef, ...] = ()
    config: RunConfig = field(default_factory=RunConfig)

    def __post_init__(self) -> None:
        if not self.specification_text and not self.drawings:
            raise ValueError("RunInput needs specification_text and/or drawings")


def make_run_input(
    *,
    run_id: str,
    team_id: str,
    case_id: str,
    corpus_snapshot_id: str,
    specification_text: Optional[str] = None,
    drawing_paths: typing.Sequence[str] = (),
    drawings: typing.Sequence[DrawingRef] = (),
    input_hash: Optional[str] = None,
    config: Optional[RunConfig] = None,
) -> RunInput:
    """Build a ``RunInput`` from upload paths, for the HTTP layer.

    The canonical field is ``RunInput.drawings``, which carries each drawing's
    media type and caption because the vision call needs both. This helper
    accepts plain ``drawing_paths`` so the API does not have to construct
    ``DrawingRef`` values itself: the media type is inferred from the file
    extension and the drawing id from the file name.

    ``input_hash`` is computed from the specification text and the drawing file
    names when it is not supplied, so two runs over the same input share a hash
    and a cached result stays attributable to the input that produced it.
    """
    refs: list[DrawingRef] = list(drawings)
    for raw_path in drawing_paths:
        name = str(raw_path).replace("\\", "/").rsplit("/", 1)[-1]
        media_type = mimetypes.guess_type(name)[0] or "image/png"
        refs.append(
            DrawingRef(
                drawing_id=name.rsplit(".", 1)[0] or name,
                media_type=media_type,
                asset_ref=str(raw_path),
                caption=name,
            )
        )
    return RunInput(
        run_id=run_id,
        team_id=team_id,
        case_id=case_id,
        corpus_snapshot_id=corpus_snapshot_id,
        input_hash=input_hash
        or content_hash(specification_text, [r.drawing_id for r in refs], case_id),
        specification_text=specification_text,
        drawings=tuple(refs),
        config=config or RunConfig(),
    )


# --------------------------------------------------------------------------- #
# Source records (produced by Teammate 2's corpus component)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Document:
    """One supplied regulation PDF inside one corpus snapshot.

    The server-side file path is deliberately absent: callers reach the file
    through ``CorpusReader.get_page_asset`` or Teammate 4's document routes.
    """

    document_id: str
    regulation: str
    """Regulation name exactly as the participant index spells it, e.g. "UN R148"."""
    title: str
    role: DocumentRole
    version_label: str
    filename: str
    checksum: str
    snapshot_id: str
    page_count: int
    index_entry: dict[str, Any] = field(default_factory=dict)
    """Verbatim index.json entry, so scope decisions can cite the index."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", DocumentRole(self.role))


@dataclass(frozen=True)
class SourcePassage:
    """A quotable unit of regulation text with full page identity."""

    passage_id: str
    document_id: str
    section_id: str
    text: str
    paragraph_label: Optional[str]
    """The **citation form**, ready to copy to the submission's ``source.paragraph``.

    For a main-body paragraph that is just the label (``6.10.4.2``); inside a
    division it carries the division (``Annex 6 Part 6 3``). None when the text
    carries no label at all. The machine form of the division lives in
    ``namespace``; this field is the human citation and the corpus builds it,
    because only the corpus knows the document's structure."""
    heading_ancestry: tuple[str, ...]
    printed_start_page: Optional[str]
    """Printed label of the first page the passage appears on; None if unverified."""
    page_span: tuple[str, ...] = ()
    """Every **printed** label the passage spans, in order, as strings.

    Printed, not physical: ``pdf_page_indices`` is the physical one. Strings
    because a printed label is not always an integer (front matter uses roman
    numerals), and a string can always carry an integer label."""
    pdf_page_indices: tuple[int, ...] = ()
    """Zero-based physical page indices, in order."""
    namespace: str = "main"
    parsing_flags: tuple[str, ...] = ()
    """Machine-readable parser warnings, e.g. "spans_pages", "table_cell"."""
    is_table: bool = False
    table_context: Optional[str] = None
    """Table headings and footnotes needed to read a table cell correctly."""

    def __post_init__(self) -> None:
        for name in (
            "heading_ancestry",
            "page_span",
            "pdf_page_indices",
            "parsing_flags",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class Section:
    section_id: str
    document_id: str
    parent_section_id: Optional[str]
    heading: str
    namespace: str
    """Annex-qualified namespace from ``namespace_for``."""
    paragraph_label: Optional[str] = None
    """The section's own citation label, e.g. "6.10.4.2" or "Annex 6 Part 6 3"."""
    primary_passage_ids: tuple[str, ...] = ()
    """Primary passages, in reading order."""
    depth: int = 0
    ordinal: int = 0
    """Position in the document's reading order; the inspection plan sorts on it."""
    parsing_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "primary_passage_ids", tuple(self.primary_passage_ids))
        object.__setattr__(self, "parsing_flags", tuple(self.parsing_flags))


@dataclass(frozen=True)
class SectionBundle:
    """A section plus the context needed to read it correctly in isolation.

    ``primary_passages`` is what the section actually says. ``context_passages``
    are inherited conditions and definitions supplied for interpretation only -
    an obligation must not be attributed to a context passage.
    """

    section: Section
    primary_passages: tuple[SourcePassage, ...]
    context_passages: tuple[SourcePassage, ...] = ()
    visual_pdf_page_indices: tuple[int, ...] = ()
    """Zero-based physical pages carrying a table or figure for this section,
    worth rendering as images. Call ``get_page_asset`` for the printed label."""
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("primary_passages", "context_passages",
                     "visual_pdf_page_indices", "notes"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class PageRef:
    document_id: str
    pdf_page_index: int
    printed_page: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class PageAsset:
    document_id: str
    pdf_page_index: int
    printed_page: Optional[str]
    """Verified printed label for this physical page, or None if unverified."""
    media_type: str
    asset_ref: str
    """Backend-local reference. Teammate 4 decides the public URL."""
    width: Optional[int] = None
    height: Optional[int] = None


# --------------------------------------------------------------------------- #
# Product understanding
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EvidenceSpan:
    """Where in the supplied input a product fact came from."""

    source: str
    """"specification" or a drawing_id."""
    quote: Optional[str] = None
    location: Optional[str] = None
    """Heading, line range, or a described region of the drawing."""


@dataclass(frozen=True)
class ProductFact:
    """One statement about the product, with its provenance and confidence.

    ``basis`` separates what the input states from what was inferred. A drawing
    shows shape; it does not establish a material specification or a measured
    photometric value, so such facts must be ``inferred`` at most.
    """

    key: str
    value: Optional[str]
    basis: str
    """"stated" | "observed" | "inferred" | "unknown"."""
    evidence: tuple[EvidenceSpan, ...] = ()
    confidence: Optional[float] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class ProductProfile:
    product_type: Optional[str] = None
    vehicle_categories: tuple[str, ...] = ()
    facts: tuple[ProductFact, ...] = ()
    components: tuple[ProductFact, ...] = ()
    functions: tuple[ProductFact, ...] = ()
    materials: tuple[ProductFact, ...] = ()
    unknowns: tuple[str, ...] = ()
    """Facts a regulation's scope depends on that the input does not establish."""
    summary: Optional[str] = None

    def fact_keys(self) -> tuple[str, ...]:
        groups = (self.facts, self.components, self.functions, self.materials)
        return tuple(f.key for group in groups for f in group)


# --------------------------------------------------------------------------- #
# Applicability
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ApplicabilityDecision:
    regulation: str
    version_label: str
    document_ids: tuple[str, ...]
    status: ApplicabilityStatus
    explanation: str
    scope_evidence: tuple[str, ...] = ()
    """Passage IDs of the scope/definition text the decision rests on."""
    conditions: tuple[str, ...] = ()
    """Conditions under which the regulation applies, in the source's own terms."""
    depends_on_unknowns: tuple[str, ...] = ()
    """ProductProfile unknowns that keep this decision uncertain."""
    index_evidence: Optional[str] = None
    """Index title/scope text, usable as a candidate signal but never as sole proof."""


# --------------------------------------------------------------------------- #
# Cross references
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReferenceRequest:
    originating_passage_id: str
    raw_reference: str
    """The reference exactly as the source text writes it."""
    target_regulation: Optional[str] = None
    target_namespace: Optional[str] = None
    target_paragraph: Optional[str] = None
    origin_document_id: Optional[str] = None
    reason: Optional[str] = None
    """Why the extractor needs this target to state the obligation correctly."""

    def key(self) -> str:
        """Identity used by the visited set, so a cycle cannot loop forever."""
        return content_hash(
            self.target_regulation,
            self.target_namespace,
            self.target_paragraph,
            self.raw_reference.strip().lower(),
            self.origin_document_id,
        )


@dataclass(frozen=True)
class ReferenceResolution:
    status: ReferenceStatus
    explanation: str
    request: Optional[ReferenceRequest] = None
    """The request this answers.

    Optional because a corpus implementation need not echo it back;
    ``follow_references`` backfills it from the request it sent, so anything
    downstream can rely on it being present."""
    candidate_passage_ids: tuple[str, ...] = ()
    """More than one candidate with status AMBIGUOUS means the corpus found
    several plausible targets and refused to pick one silently."""
    resolved_document_id: Optional[str] = None
    resolved_section_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ReferenceStatus(self.status))
        object.__setattr__(
            self, "candidate_passage_ids", tuple(self.candidate_passage_ids)
        )

    @property
    def is_usable(self) -> bool:
        return self.status is ReferenceStatus.RESOLVED and bool(self.candidate_passage_ids)


# --------------------------------------------------------------------------- #
# Draft obligations and coverage
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NumericValue:
    """A number the obligation turns on, kept machine-readable for review.

    Teammate 3 compares these against the cited passage text to catch
    transcription errors that read fluently.
    """

    quantity: Optional[str]
    """What is measured, e.g. "luminous intensity"."""
    value: Optional[str]
    """Kept as a string so "0,5" and tolerances survive unchanged."""
    unit: Optional[str] = None
    comparator: Optional[str] = None
    """e.g. ">=", "<=", "between", "=", "max", "min"."""


@dataclass(frozen=True)
class DraftRequirement:
    """One atomic obligation with its source evidence.

    A draft may cite several supporting passages internally, but
    ``primary_passage_id`` must on its own support the stated obligation,
    because the official submission carries one source object. If no single
    passage supports the chosen formulation, raise a review finding instead of
    choosing a citation that does not.
    """

    draft_id: str
    text: str
    supporting_passage_ids: tuple[str, ...]
    primary_passage_id: str
    obligation_kind: ObligationKind = ObligationKind.MANDATORY
    applicability_condition: Optional[str] = None
    """When the obligation applies, in the source's terms. None means unconditional."""
    applicability_reason: Optional[str] = None
    """Why this obligation applies to this product."""
    exceptions: tuple[str, ...] = ()
    values: tuple[NumericValue, ...] = ()
    unresolved_references: tuple[ReferenceResolution, ...] = ()
    document_id: Optional[str] = None
    section_id: Optional[str] = None
    prompt_version: Optional[str] = None
    revision_of: Optional[str] = None
    """draft_id this draft replaces, when produced by revise_extraction."""
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        if self.primary_passage_id not in self.supporting_passage_ids:
            raise ValueError(
                f"draft {self.draft_id}: primary_passage_id "
                f"{self.primary_passage_id!r} is not in supporting_passage_ids"
            )


@dataclass(frozen=True)
class CoverageItem:
    """Recorded inspection status for one planned unit of source.

    This records what was inspected. It is not a claim of exhaustive legal
    compliance, and a PROCESSED section with zero drafts is a legitimate result.
    """

    target_kind: TargetKind
    target_id: str
    status: CoverageStatus
    explanation: Optional[str] = None
    inspected_passage_ids: tuple[str, ...] = ()
    draft_ids: tuple[str, ...] = ()
    document_id: Optional[str] = None


@dataclass(frozen=True)
class ModelUsage:
    """Measured usage. Token counts stay None when the provider does not report them."""

    calls: int = 0
    cached_calls: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms_total: float = 0.0
    by_model: dict[str, int] = field(default_factory=dict)
    """model id -> call count."""

    def merged_with(self, other: "ModelUsage") -> "ModelUsage":
        def add(a: Optional[int], b: Optional[int]) -> Optional[int]:
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)

        by_model = dict(self.by_model)
        for model, count in other.by_model.items():
            by_model[model] = by_model.get(model, 0) + count
        return ModelUsage(
            calls=self.calls + other.calls,
            cached_calls=self.cached_calls + other.cached_calls,
            input_tokens=add(self.input_tokens, other.input_tokens),
            output_tokens=add(self.output_tokens, other.output_tokens),
            latency_ms_total=self.latency_ms_total + other.latency_ms_total,
            by_model=by_model,
        )


@dataclass(frozen=True)
class PipelineDraft:
    """Everything the extraction component returns. Teammate 3 reviews this."""

    run_id: str
    corpus_snapshot_id: str
    product_profile: ProductProfile
    applicability_decisions: tuple[ApplicabilityDecision, ...] = ()
    draft_requirements: tuple[DraftRequirement, ...] = ()
    coverage: tuple[CoverageItem, ...] = ()
    unresolved_references: tuple[ReferenceResolution, ...] = ()
    warnings: tuple[str, ...] = ()
    usage: ModelUsage = field(default_factory=ModelUsage)
    partial: bool = True
    """True until every planned section is processed or explicitly excluded."""
    prompt_versions: dict[str, str] = field(default_factory=dict)
    is_synthetic_fixture: bool = False
    """Set by test fixtures so no caller can present fixture output as a real run."""

    def draft_by_id(self, draft_id: str) -> Optional[DraftRequirement]:
        for draft in self.draft_requirements:
            if draft.draft_id == draft_id:
                return draft
        return None

    def coverage_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {status.value: 0 for status in CoverageStatus}
        for item in self.coverage:
            counts[item.status.value] += 1
        return counts


# --------------------------------------------------------------------------- #
# Review, export and evaluation (Teammate 3)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReviewFinding:
    """One thing review found wrong, or needs, about a draft or a section.

    Addressing is by ``target_kind`` + ``target_id`` rather than a bare
    ``draft_id`` because a finding can also concern a section ("this section was
    never read") or the whole run ("review did not finish"). Use the
    ``for_draft`` / ``for_section`` / ``for_run`` constructors, and the
    ``draft_id`` property when you only care about draft-level findings.

    ``category``, ``severity`` and ``target_kind`` accept their plain string
    values and are coerced, so a caller holding a string constant does not need
    to import the enums. An unrecognised value raises rather than silently
    becoming "other".
    """

    target_id: str
    """A draft_id, section_id, passage_id or run_id, matching target_kind."""
    category: FindingCategory
    explanation: str
    target_kind: TargetKind = TargetKind.DRAFT
    finding_id: str = ""
    """Derived from the content when left empty, so callers need not invent ids."""
    severity: FindingSeverity = FindingSeverity.REVISION_REQUESTED
    supporting_passage_ids: tuple[str, ...] = ()
    requested_context: tuple[str, ...] = ()
    """Section or passage **IDs** the reviewer wants pulled in before
    re-extraction. ``revise_extraction`` resolves these: a passage id is fetched
    directly and a section id pulls in that section's primary and context
    passages. Prose about what is missing belongs in ``explanation``; this field
    is machine-actionable, so a sentence here resolves to nothing."""
    requested_correction: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", FindingCategory(self.category))
        object.__setattr__(self, "severity", FindingSeverity(self.severity))
        object.__setattr__(self, "target_kind", TargetKind(self.target_kind))
        object.__setattr__(
            self, "supporting_passage_ids", tuple(self.supporting_passage_ids)
        )
        object.__setattr__(self, "requested_context", tuple(self.requested_context))
        if not self.finding_id:
            object.__setattr__(
                self,
                "finding_id",
                "fnd-"
                + content_hash(
                    self.target_kind.value,
                    self.target_id,
                    self.category.value,
                    self.explanation,
                )[:12],
            )

    @classmethod
    def for_draft(
        cls, draft_id: str, category: FindingCategory | str, explanation: str, **kwargs: Any
    ) -> "ReviewFinding":
        return cls(
            target_id=draft_id,
            category=category,
            explanation=explanation,
            target_kind=TargetKind.DRAFT,
            **kwargs,
        )

    @classmethod
    def for_section(
        cls, section_id: str, category: FindingCategory | str, explanation: str, **kwargs: Any
    ) -> "ReviewFinding":
        return cls(
            target_id=section_id,
            category=category,
            explanation=explanation,
            target_kind=TargetKind.SECTION,
            **kwargs,
        )

    @classmethod
    def for_run(
        cls, run_id: str, category: FindingCategory | str, explanation: str, **kwargs: Any
    ) -> "ReviewFinding":
        """A finding about the review itself, e.g. that it did not finish.

        Use this instead of a draft-level finding with no draft id.
        """
        return cls(
            target_id=run_id,
            category=category,
            explanation=explanation,
            target_kind=TargetKind.RUN,
            **kwargs,
        )

    @property
    def draft_id(self) -> Optional[str]:
        """The draft this finding concerns, or None if it concerns something else."""
        return self.target_id if self.target_kind is TargetKind.DRAFT else None


@dataclass(frozen=True)
class QualityReport:
    """What review decided, and what it could not settle."""

    run_id: str
    accepted_draft_ids: tuple[str, ...] = ()
    findings: tuple[ReviewFinding, ...] = ()
    duplicate_mapping: dict[str, str] = field(default_factory=dict)
    """removed draft_id -> retained draft_id, so every removal stays explainable."""
    rejected: dict[str, str] = field(default_factory=dict)
    """draft_id -> reason."""
    remaining_issues: tuple[ReviewFinding, ...] = ()
    """Issues review could not settle, as findings rather than prose.

    A remaining issue carries its target and its evidence, so "we ran out of
    time on these three drafts" stays actionable instead of becoming a sentence
    nobody can act on."""
    export_mapping: dict[str, str] = field(default_factory=dict)
    """draft_id -> requirement_id assigned during export.

    This is the audit trail from a submitted requirement back to the draft and
    the source evidence that produced it."""
    partial: bool = False
    usage: ModelUsage = field(default_factory=ModelUsage)

    def __post_init__(self) -> None:
        object.__setattr__(self, "accepted_draft_ids", tuple(self.accepted_draft_ids))
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(self, "remaining_issues", tuple(self.remaining_issues))
        object.__setattr__(self, "duplicate_mapping", dict(self.duplicate_mapping))
        object.__setattr__(self, "rejected", dict(self.rejected))
        object.__setattr__(self, "export_mapping", dict(self.export_mapping))

    def with_export_mapping(self, mapping: dict[str, str]) -> "QualityReport":
        """Return a copy carrying the export mapping.

        The records are frozen, so export records its ids this way instead of
        assigning to a built report - an assignment would raise, and a caller
        that swallowed the error would lose the audit trail silently.
        """
        return dataclasses.replace(self, export_mapping=dict(mapping))

    def findings_for_draft(self, draft_id: str) -> tuple[ReviewFinding, ...]:
        return tuple(
            f
            for f in self.findings
            if f.target_kind is TargetKind.DRAFT and f.target_id == draft_id
        )

    def revision_requested(self) -> bool:
        return any(
            f.severity
            in (FindingSeverity.REVISION_REQUESTED, FindingSeverity.BLOCKING)
            for f in self.findings
        )


@dataclass(frozen=True)
class ValidationError:
    path: str
    """JSON path into the submission, e.g. "requirements[12].source.page"."""
    message: str
    code: Optional[str] = None


@dataclass(frozen=True)
class ValidationReport:
    """Result of checking a submission against the official rules.

    ``official_validator_ran`` exists because "our own checks passed" and "the
    organizers' validator passed" are different claims, and only the second one
    counts. A report with ``valid=True`` and ``official_validator_ran=False``
    must never be presented as a validated submission.
    """

    valid: bool
    errors: tuple[ValidationError, ...] = ()
    official_validator_ran: bool = False
    official_validator_output: Optional[str] = None
    """Verbatim output of the supplied validator, kept for the run record."""
    schema_identity: Optional[str] = None
    """Which schema was checked against, e.g. a path plus checksum."""
    index_identity: Optional[str] = None
    checked_at: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "errors", tuple(self.errors))


@dataclass(frozen=True)
class EvaluationReport:
    """Locally measurable metrics only.

    ``semantic_*`` and ``platform_*`` stay None unless a real measurement
    produced them. The local scorer measures regulation/page coverage, not
    semantic requirement matching, so a blank semantic score is the correct
    output, not a missing one.
    """

    regulation_precision: Optional[float] = None
    regulation_recall: Optional[float] = None
    regulation_f1: Optional[float] = None
    page_precision: Optional[float] = None
    page_recall: Optional[float] = None
    page_f1: Optional[float] = None
    counts: dict[str, int] = field(default_factory=dict)
    error_categories: dict[str, int] = field(default_factory=dict)
    semantic_score: Optional[float] = None
    platform_score: Optional[float] = None
    notes: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Run reporting (Teammate 4)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunEvent:
    stage: str
    message: str
    timestamp: str
    """ISO 8601 UTC."""
    completed: Optional[int] = None
    total: Optional[int] = None
    affected_ids: tuple[str, ...] = ()
    level: str = "info"


@dataclass(frozen=True)
class RequirementView:
    """One requirement joined to its resolved source evidence, ready for display."""

    requirement_id: str
    draft_id: str
    text: str
    regulation: str
    version_label: str
    printed_page: Optional[str]
    pdf_page_index: Optional[int]
    paragraph_label: Optional[str]
    namespace: str
    primary_passage: Optional[SourcePassage] = None
    supporting_passages: tuple[SourcePassage, ...] = ()
    applicability_condition: Optional[str] = None
    findings: tuple[ReviewFinding, ...] = ()


@dataclass(frozen=True)
class RunView:
    run_id: str
    state: RunState
    stage: Optional[str] = None
    product_profile: Optional[ProductProfile] = None
    applicability_decisions: tuple[ApplicabilityDecision, ...] = ()
    requirements: tuple[RequirementView, ...] = ()
    coverage: tuple[CoverageItem, ...] = ()
    coverage_partial: bool = True
    findings: tuple[ReviewFinding, ...] = ()
    usage: ModelUsage = field(default_factory=ModelUsage)
    validation: Optional[ValidationReport] = None
    evaluation: Optional[EvaluationReport] = None
    events: tuple[RunEvent, ...] = ()
    errors: tuple[str, ...] = ()
    is_fixture_data: bool = False
    """True when the view was built from labelled fixtures rather than a real run."""


# --------------------------------------------------------------------------- #
# Model adapter boundary
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ImagePayload:
    """An image handed to a vision call."""

    media_type: str
    data_base64: Optional[str] = None
    asset_ref: Optional[str] = None
    """Backend-local file reference; the adapter reads and encodes it."""
    caption: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.data_base64 and not self.asset_ref:
            raise ValueError("ImagePayload needs data_base64 or asset_ref")


@dataclass(frozen=True)
class LLMResponse:
    """Result of one structured model call.

    Valid JSON is not evidence that the content is correct, so ``warnings``
    carries anything the adapter noticed, and callers still verify meaning
    against source passages.
    """

    data: Any
    """Parsed structured output, already schema-checked by the adapter."""
    provider: str
    model: str
    task: str
    latency_ms: float
    usage: ModelUsage = field(default_factory=ModelUsage)
    warnings: tuple[str, ...] = ()
    cached: bool = False
    raw_text: Optional[str] = None
    finish_reason: Optional[str] = None


class LLMConfigurationError(RuntimeError):
    """Raised when no usable provider credentials are configured.

    This is explicit by design: the application must not silently fall back to
    fabricated output when the model is unavailable.
    """


class LLMCallError(RuntimeError):
    """A call failed after the configured retries, or returned unusable output."""


class ModelBudgetExceeded(RuntimeError):
    """The run hit ``RunConfig.max_model_calls``."""


# --------------------------------------------------------------------------- #
# Interfaces
# --------------------------------------------------------------------------- #


@runtime_checkable
class CorpusReader(Protocol):
    """Read-only source access, bound to exactly one corpus snapshot.

    Implemented by Teammate 2. No other component queries SQLite or touches the
    corpus schema. A reader handed to the agent or quality component must not be
    able to mutate the snapshot.
    """

    @property
    def snapshot_id(self) -> str: ...

    def list_documents(self, role: DocumentRole | str = DocumentRole.BASE) -> list[Document]:
        """Documents of that role in this snapshot, in index order."""

    def read_scope(self, document_id: str) -> list[SourcePassage]:
        """Scope, application and definition passages used for applicability."""

    def list_sections(self, document_id: str) -> list[Section]:
        """All sections, in document reading order (``Section.ordinal``)."""

    def read_section(self, section_id: str, include_context: bool = True) -> SectionBundle:
        """One section with inherited conditions and relevant definitions."""

    def get_passages(self, passage_ids: typing.Sequence[str]) -> list[SourcePassage]:
        """Passages for the given IDs. Unknown IDs are omitted, not invented."""

    def resolve_reference(self, request: ReferenceRequest) -> ReferenceResolution:
        """Resolve a cross reference within the correct document and namespace.

        Returns AMBIGUOUS with several candidates rather than silently picking one.
        """

    def get_page_asset(self, document_id: str, pdf_page_index: int) -> PageAsset:
        """Render or fetch one physical page. ``pdf_page_index`` is zero-based."""

    def get_document(self, document_id: str) -> Document:
        """One document's metadata."""

    def list_reference_candidates(
        self, passage_ids: typing.Sequence[str]
    ) -> list[ReferenceRequest]:
        """Cross references the corpus already detected in those passages.

        Returned ready to hand to ``resolve_reference``. This matters for
        recall: without it, following a reference depends entirely on the model
        noticing it in the text, and a missed reference is silent.
        """


@runtime_checkable
class CorpusSearch(Protocol):
    """Optional search, for navigation and reference resolution only.

    Separate from ``CorpusReader`` because search is explicitly **not** the
    coverage mechanism - systematic reading is - so an implementation must not
    be required to provide it.
    """

    def search(
        self, query: str, document_id: str | None = None, limit: int = 20
    ) -> list[SourcePassage]: ...


@runtime_checkable
class LLMAdapter(Protocol):
    """The single structured-output/vision boundary.

    Owned by Anushree; used by the agent component and, with its own prompts,
    by Teammate 3's review. ``task`` names the call site for caching, usage
    attribution and per-task model overrides.
    """

    def generate_json(
        self,
        task: str,
        instructions: str,
        payload: Any,
        schema: dict[str, Any],
        images: typing.Sequence[ImagePayload] | None = None,
        *,
        prompt_version: str = "v1",
        max_output_tokens: int | None = None,
    ) -> LLMResponse: ...


def make_event(
    stage: str,
    message: str,
    *,
    completed: Optional[int] = None,
    total: Optional[int] = None,
    affected_ids: typing.Sequence[str] = (),
    level: str = "info",
) -> RunEvent:
    """Build a ``RunEvent`` with the timestamp filled in.

    ``emit_event`` expects a ``RunEvent``, not a dict. This helper exists so no
    component has to format the timestamp itself.
    """
    import datetime as _datetime

    return RunEvent(
        stage=stage,
        message=message,
        timestamp=_datetime.datetime.now(_datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        completed=completed,
        total=total,
        affected_ids=tuple(affected_ids),
        level=level,
    )


EmitEvent = typing.Callable[[RunEvent], None]
"""Progress callback. The agent component must work when this does nothing."""


def null_emit(event: RunEvent) -> None:
    """Default ``EmitEvent`` so no component depends on the API module."""
    return None


@runtime_checkable
class ExtractionComponent(Protocol):
    """Anushree's public boundary, called by Teammate 4's composition layer."""

    def run_extraction(
        self,
        run_input: RunInput,
        corpus: CorpusReader,
        llm: LLMAdapter,
        emit_event: EmitEvent = null_emit,
    ) -> PipelineDraft: ...

    def revise_extraction(
        self,
        run_input: RunInput,
        draft: PipelineDraft,
        findings: typing.Sequence[ReviewFinding],
        corpus: CorpusReader,
        llm: LLMAdapter,
        emit_event: EmitEvent = null_emit,
    ) -> PipelineDraft: ...


@runtime_checkable
class QualityComponent(Protocol):
    """Teammate 3's public boundary."""

    def review_requirements(
        self,
        run_input: RunInput,
        draft: PipelineDraft,
        corpus: CorpusReader,
        llm: LLMAdapter,
        emit_event: EmitEvent = null_emit,
    ) -> QualityReport: ...

    def build_submission(
        self,
        run_input: RunInput,
        draft: PipelineDraft,
        report: QualityReport,
        corpus: CorpusReader,
    ) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- #
# JSON codec
#
# Teammate 4 serialises these records over HTTP and the agent component writes
# them to per-run artifacts. One codec here keeps the wire format identical
# everywhere, so frontend types can be generated from one source.
# --------------------------------------------------------------------------- #


def to_jsonable(value: Any) -> Any:
    """Recursively convert records to JSON-safe primitives.

    Tuples become lists and enums become their values, so the wire format never
    depends on Python specifics.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in value]
    return value


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Return (inner type, is_optional) for ``X | None`` and ``Optional[X]``."""
    origin = typing.get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        optional = len(args) != len(typing.get_args(annotation))
        if len(args) == 1:
            return args[0], optional
        return Union[tuple(args)], optional  # type: ignore[return-value]
    return annotation, False


def _coerce(annotation: Any, value: Any) -> Any:
    annotation, optional = _unwrap_optional(annotation)
    if value is None:
        if not optional:
            # Keep None rather than inventing a value; __post_init__ decides.
            return None
        return None

    origin = typing.get_origin(annotation)
    if origin in (tuple, list, set, frozenset):
        args = typing.get_args(annotation)
        item_type = args[0] if args and args[0] is not Ellipsis else Any
        items = [_coerce(item_type, v) for v in value]
        return tuple(items) if origin is tuple else origin(items)
    if origin is dict:
        args = typing.get_args(annotation)
        key_t, val_t = (args + (Any, Any))[:2]
        return {_coerce(key_t, k): _coerce(val_t, v) for k, v in value.items()}
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return annotation(value)
    if dataclasses.is_dataclass(annotation) and isinstance(value, dict):
        return from_jsonable(annotation, value)
    return value


_T = typing.TypeVar("_T")


def from_jsonable(record_type: type[_T], data: dict[str, Any]) -> _T:
    """Rebuild a record from ``to_jsonable`` output.

    Unknown keys are ignored so that adding a field to a record does not break
    an older saved artifact. Missing keys fall back to the record's default,
    and a missing required field raises, which is the intended loud failure.
    """
    hints = typing.get_type_hints(record_type, include_extras=False)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(record_type):  # type: ignore[arg-type]
        if f.name not in data:
            continue
        kwargs[f.name] = _coerce(hints.get(f.name, Any), data[f.name])
    return record_type(**kwargs)  # type: ignore[arg-type]


__all__ = [name for name in dir() if not name.startswith("_")]
