"""SYNTHETIC example records for every shared contract (owner: Anushree).

**Fixtures, not results.** ``PipelineDraft.is_synthetic_fixture`` and
``RunView.is_fixture_data`` are set to true here so a UI or an export that
renders one cannot present it as a real extraction. Teammate 4's interface must
keep fixture mode visibly separate; this flag is how.

Purpose: Teammate 3 can build review and export, and Teammate 4 can build the
API and UI, against a complete and valid set of records before extraction or
PDF ingestion is finished.
"""

from __future__ import annotations

from certivity.contracts import (
    ApplicabilityDecision,
    ApplicabilityStatus,
    CoverageItem,
    CoverageStatus,
    DrawingRef,
    DraftRequirement,
    EvidenceSpan,
    FindingCategory,
    FindingSeverity,
    ModelUsage,
    NumericValue,
    ObligationKind,
    PipelineDraft,
    ProductFact,
    ProductProfile,
    QualityReport,
    ReferenceRequest,
    ReferenceResolution,
    ReferenceStatus,
    RequirementView,
    ReviewFinding,
    RunConfig,
    RunEvent,
    RunInput,
    RunState,
    RunView,
    TargetKind,
    ValidationReport,
    content_hash,
)
from tests.fixtures.contracts import corpus as syn

RUN_ID = "run-synthetic-0001"
TEAM_ID = "team-fixture"
CASE_ID = "synthetic-signalling-device"

RUN_INPUT = RunInput(
    run_id=RUN_ID,
    team_id=TEAM_ID,
    case_id=CASE_ID,
    corpus_snapshot_id=syn.SNAPSHOT_ID,
    input_hash=content_hash("synthetic-fixture-input", CASE_ID),
    specification_text=(
        "SYNTHETIC FIXTURE SPECIFICATION\n\n"
        "Rear signalling device for a passenger car (category M1). The device "
        "emits red light through a polycarbonate lens and signals the presence "
        "of the vehicle to other road users. The luminous intensity and any "
        "category marking are not specified in this document."
    ),
    drawings=(
        DrawingRef(
            drawing_id="fixture-exploded-view",
            media_type="image/png",
            asset_ref="synthetic://drawings/exploded-view.png",
            caption="Synthetic exploded view (fixture)",
        ),
    ),
    config=RunConfig(max_model_calls=40, passages_per_batch=4),
)

PRODUCT_PROFILE = ProductProfile(
    product_type="synthetic signalling device",
    vehicle_categories=("M1",),
    facts=(
        ProductFact(
            key="colour",
            value="red",
            basis="stated",
            evidence=(EvidenceSpan(source="specification", quote="emits red light"),),
        ),
        ProductFact(key="marking", value=None, basis="unknown"),
    ),
    components=(
        ProductFact(
            key="lens",
            value="polycarbonate lens",
            basis="stated",
            evidence=(EvidenceSpan(source="specification", quote="polycarbonate lens"),),
        ),
    ),
    functions=(
        ProductFact(key="signalling", value="signals vehicle presence", basis="stated"),
    ),
    unknowns=("luminous_intensity", "marking"),
    summary="A synthetic rear signalling device for an M1 vehicle.",
)

APPLICABILITY = (
    ApplicabilityDecision(
        regulation=syn.REG_A,
        version_label="00 series",
        document_ids=(syn.DOC_A,),
        status=ApplicabilityStatus.APPLICABLE,
        explanation=(
            "The scope passage covers signalling devices of category SX on M1 "
            "vehicles, which matches the product."
        ),
        scope_evidence=(syn.A_SCOPE_P1.passage_id,),
        conditions=("device is a category SX signalling device on an M1 vehicle",),
        depends_on_unknowns=("marking",),
        index_evidence="The index entry describes signalling devices.",
    ),
    ApplicabilityDecision(
        regulation=syn.REG_B,
        version_label="01 series",
        document_ids=(syn.DOC_B,),
        status=ApplicabilityStatus.EXCLUDED,
        explanation=(
            "The scope passage covers welded pressure vessels on N3 vehicles; the "
            "product is neither."
        ),
        scope_evidence=(syn.B_SCOPE_P1.passage_id,),
        index_evidence="The index entry describes a different kind of object.",
    ),
)

# A requirement that keeps its range together and its exception attached.
DRAFT_INTENSITY = DraftRequirement(
    draft_id="drf-fixture-0001",
    text=(
        "The luminous intensity shall be between 4 cd and 12 cd when measured along "
        'the reference axis, except for devices bearing the marking "R2", for which '
        "it shall be between 2 cd and 6 cd."
    ),
    supporting_passage_ids=(syn.A_SPEC_INTENSITY.passage_id, syn.A_DEF_P2.passage_id),
    primary_passage_id=syn.A_SPEC_INTENSITY.passage_id,
    obligation_kind=ObligationKind.MANDATORY,
    applicability_condition="device is a category SX signalling device on an M1 vehicle",
    applicability_reason="The product is a signalling device on an M1 vehicle.",
    exceptions=('devices bearing the marking "R2": between 2 cd and 6 cd',),
    values=(
        NumericValue(
            quantity="luminous intensity", value="4 to 12", unit="cd", comparator="between"
        ),
    ),
    document_id=syn.DOC_A,
    section_id=syn.A_SPECS,
    prompt_version="v1",
)

# A requirement whose incorporated reference could not be resolved. The run must
# say so rather than presenting it as complete.
UNRESOLVED_REFERENCE = ReferenceResolution(
    request=ReferenceRequest(
        origin_passage_id=syn.A_SPEC_REFERENCE.passage_id,
        raw_reference="SYNTHETIC REG Z",
        target_regulation="SYNTHETIC REG Z",
        target_paragraph="1",
        origin_document_id=syn.DOC_A,
        reason="The referenced regulation's provisions are incorporated.",
    ),
    status=ReferenceStatus.OUTSIDE_CORPUS,
    explanation="SYNTHETIC REG Z is not in this snapshot",
)

DRAFT_TEST = DraftRequirement(
    draft_id="drf-fixture-0002",
    text=(
        "The device shall comply with the test procedure of Annex 1, paragraph 2.1, "
        "measured at an ambient temperature of 25 °C after 30 minutes of operation."
    ),
    supporting_passage_ids=(syn.A_SPEC_REFERENCE.passage_id, syn.A_ANNEX_P1.passage_id),
    primary_passage_id=syn.A_SPEC_REFERENCE.passage_id,
    applicability_condition="device is a category SX signalling device on an M1 vehicle",
    applicability_reason="The product is a signalling device on an M1 vehicle.",
    values=(
        NumericValue(
            quantity="ambient temperature", value="25", unit="°C", comparator="="
        ),
    ),
    unresolved_references=(UNRESOLVED_REFERENCE,),
    document_id=syn.DOC_A,
    section_id=syn.A_SPECS,
    prompt_version="v1",
)

DRAFT_DEFINITION = DraftRequirement(
    draft_id="drf-fixture-0003",
    text=(
        "The reference axis marked on the device shall not deviate by more than "
        "1,5 degrees from the geometric centre line."
    ),
    supporting_passage_ids=(syn.A_DEF_P2.passage_id,),
    primary_passage_id=syn.A_DEF_P2.passage_id,
    obligation_kind=ObligationKind.DEFINITION_RULE,
    applicability_reason="The definition imposes a measurable limit.",
    values=(
        NumericValue(
            quantity="reference axis deviation",
            value="1,5",
            unit="degrees",
            comparator="<=",
        ),
    ),
    document_id=syn.DOC_A,
    section_id=syn.A_DEFS,
    prompt_version="v1",
)

COVERAGE = (
    CoverageItem(
        target_kind=TargetKind.DOCUMENT,
        target_id=syn.DOC_A,
        status=CoverageStatus.PENDING,
        explanation="3 processed, 1 pending, 0 failed, 1 excluded",
        document_id=syn.DOC_A,
    ),
    CoverageItem(
        target_kind=TargetKind.SECTION,
        target_id=syn.A_CONTENTS,
        status=CoverageStatus.EXCLUDED,
        explanation="Navigation apparatus, not substantive text: 'Table of contents'",
        document_id=syn.DOC_A,
    ),
    CoverageItem(
        target_kind=TargetKind.SECTION,
        target_id=syn.A_SCOPE,
        status=CoverageStatus.PROCESSED,
        inspected_passage_ids=(syn.A_SCOPE_P1.passage_id,),
        document_id=syn.DOC_A,
    ),
    CoverageItem(
        target_kind=TargetKind.SECTION,
        target_id=syn.A_DEFS,
        status=CoverageStatus.PROCESSED,
        inspected_passage_ids=(syn.A_DEF_P1.passage_id, syn.A_DEF_P2.passage_id),
        draft_ids=(DRAFT_DEFINITION.draft_id,),
        document_id=syn.DOC_A,
    ),
    CoverageItem(
        target_kind=TargetKind.SECTION,
        target_id=syn.A_SPECS,
        status=CoverageStatus.PROCESSED,
        inspected_passage_ids=(
            syn.A_SPEC_COLOUR.passage_id,
            syn.A_SPEC_INTENSITY.passage_id,
            syn.A_SPEC_REFERENCE.passage_id,
        ),
        draft_ids=(DRAFT_INTENSITY.draft_id, DRAFT_TEST.draft_id),
        document_id=syn.DOC_A,
    ),
    CoverageItem(
        target_kind=TargetKind.SECTION,
        target_id=syn.A_ANNEX_TEST,
        status=CoverageStatus.PENDING,
        explanation="Not read: RunConfig.max_sections_per_document was reached.",
        document_id=syn.DOC_A,
    ),
)

PIPELINE_DRAFT = PipelineDraft(
    run_id=RUN_ID,
    corpus_snapshot_id=syn.SNAPSHOT_ID,
    product_profile=PRODUCT_PROFILE,
    applicability_decisions=APPLICABILITY,
    draft_requirements=(DRAFT_INTENSITY, DRAFT_TEST, DRAFT_DEFINITION),
    coverage=COVERAGE,
    unresolved_references=(UNRESOLVED_REFERENCE,),
    warnings=(
        "1 of 4 planned section(s) were not processed; coverage is partial",
    ),
    usage=ModelUsage(calls=7, input_tokens=18000, output_tokens=4200, by_model={"synthetic-fixture-model": 7}),
    partial=True,
    prompt_versions={"extract_obligations": "v1", "product_profile": "v1"},
    is_synthetic_fixture=True,
)

FINDING_MISSING_CONDITION = ReviewFinding(
    finding_id="fnd-fixture-0001",
    target_kind=TargetKind.DRAFT,
    target_id=DRAFT_DEFINITION.draft_id,
    category=FindingCategory.MISSING_CONDITION,
    explanation=(
        "The obligation does not state which devices the reference-axis limit applies "
        "to; the scope condition may need to be carried in."
    ),
    severity=FindingSeverity.REVISION_REQUESTED,
    supporting_passage_ids=(syn.A_DEF_P2.passage_id,),
    requested_context=(syn.A_SCOPE,),
)

QUALITY_REPORT = QualityReport(
    run_id=RUN_ID,
    accepted_draft_ids=(DRAFT_INTENSITY.draft_id, DRAFT_TEST.draft_id),
    findings=(FINDING_MISSING_CONDITION,),
    duplicate_map={},
    rejected={},
    remaining_issues=(
        "one unresolved cross reference to a regulation outside the snapshot",
    ),
    partial=True,
)

REQUIREMENT_VIEWS = (
    RequirementView(
        requirement_id="REQ-0001",
        draft_id=DRAFT_INTENSITY.draft_id,
        text=DRAFT_INTENSITY.text,
        regulation=syn.REG_A,
        version_label="00 series",
        printed_page=syn.A_SPEC_INTENSITY.printed_start_page,
        pdf_page_index=syn.A_SPEC_INTENSITY.pdf_page_indices[0],
        paragraph_label=syn.A_SPEC_INTENSITY.paragraph_label,
        namespace=syn.A_SPEC_INTENSITY.namespace,
        primary_passage=syn.A_SPEC_INTENSITY,
        supporting_passages=(syn.A_SPEC_INTENSITY, syn.A_DEF_P2),
        applicability_condition=DRAFT_INTENSITY.applicability_condition,
    ),
    RequirementView(
        requirement_id="REQ-0002",
        draft_id=DRAFT_TEST.draft_id,
        text=DRAFT_TEST.text,
        regulation=syn.REG_A,
        version_label="00 series",
        printed_page=syn.A_SPEC_REFERENCE.printed_start_page,
        pdf_page_index=syn.A_SPEC_REFERENCE.pdf_page_indices[0],
        paragraph_label=syn.A_SPEC_REFERENCE.paragraph_label,
        namespace=syn.A_SPEC_REFERENCE.namespace,
        primary_passage=syn.A_SPEC_REFERENCE,
        supporting_passages=(syn.A_SPEC_REFERENCE, syn.A_ANNEX_P1),
        applicability_condition=DRAFT_TEST.applicability_condition,
    ),
)

RUN_VIEW = RunView(
    run_id=RUN_ID,
    state=RunState.PARTIAL,
    stage="completed",
    product_profile=PRODUCT_PROFILE,
    applicability_decisions=APPLICABILITY,
    requirements=REQUIREMENT_VIEWS,
    coverage=COVERAGE,
    coverage_partial=True,
    findings=(FINDING_MISSING_CONDITION,),
    usage=PIPELINE_DRAFT.usage,
    validation=ValidationReport(valid=True, schema_identity="fixture-schema"),
    evaluation=None,
    events=(
        RunEvent(
            stage="extract",
            message="SYNTHETIC REG A 3: 2 obligation(s)",
            timestamp="2026-10-24T09:00:00+00:00",
            completed=3,
            total=4,
            affected_ids=(syn.A_SPECS,),
        ),
    ),
    is_fixture_data=True,
)
