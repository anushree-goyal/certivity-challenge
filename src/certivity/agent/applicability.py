"""Scope-based applicability (owner: Anushree).

Two stages, deliberately separated:

1. Shortlist candidates from the **complete** allowed index. This is navigation.
2. Decide each candidate from the regulation's own scope and definition
   passages. This is the decision, and it is the only thing recorded as one.

Neither stage may hardcode a case's answer. The index drives stage 1 and the
source text drives stage 2, so a different input produces a different selection
without any source-code edit.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from certivity.contracts import (
    ApplicabilityDecision,
    ApplicabilityStatus,
    Document,
    DocumentRole,
    ModelBudgetExceeded,
    ProductProfile,
    SourcePassage,
)
from certivity.agent.context import RunContext
from certivity.agent.profile import profile_payload
from certivity.agent.prompts import (
    CANDIDATE_SCOPE_INSTRUCTIONS,
    CANDIDATE_SCOPE_SCHEMA,
    DOCUMENT_SCOPE_INSTRUCTIONS,
    DOCUMENT_SCOPE_SCHEMA,
    PROMPT_VERSIONS,
)

MAX_SCOPE_PASSAGES = 60
"""Scope and definition passages per decision call. A regulation whose scope
section exceeds this is truncated, and the truncation is recorded as a warning
rather than hidden."""


def _passage_payload(passages: Iterable[SourcePassage]) -> list[dict[str, Any]]:
    return [
        {
            "passage_id": p.passage_id,
            "namespace": p.namespace,
            "paragraph_label": p.paragraph_label,
            "heading_ancestry": list(p.heading_ancestry),
            "printed_page": p.printed_start_page,
            "text": p.text,
            "table_context": p.table_context,
        }
        for p in passages
    ]


def _group_by_regulation(documents: Iterable[Document]) -> dict[str, list[Document]]:
    grouped: dict[str, list[Document]] = defaultdict(list)
    for document in documents:
        grouped[document.regulation].append(document)
    return dict(grouped)


def shortlist_candidates(
    ctx: RunContext, profile: ProductProfile, documents: list[Document]
) -> dict[str, tuple[bool, str, tuple[str, ...]]]:
    """Return regulation -> (is_candidate, reason, depends_on_unknowns).

    Every regulation in the index gets an entry. A regulation the model does not
    mention defaults to **candidate**, because an unmentioned regulation has not
    been reasoned about and dropping it silently would be the one failure this
    stage exists to prevent.
    """
    by_regulation = _group_by_regulation(documents)
    index_payload = [
        {
            "regulation": regulation,
            "titles": sorted({d.title for d in docs}),
            "version_labels": sorted({d.version_label for d in docs}),
            "index_entry": docs[0].index_entry or None,
        }
        for regulation, docs in sorted(by_regulation.items())
    ]

    ctx.event(
        "applicability",
        f"Screening the full index: {len(index_payload)} regulations",
        total=len(index_payload),
    )

    results: dict[str, tuple[bool, str, tuple[str, ...]]] = {}
    try:
        response = ctx.call_json(
            "candidate_scope",
            CANDIDATE_SCOPE_INSTRUCTIONS,
            {"product": profile_payload(profile), "regulation_index": index_payload},
            CANDIDATE_SCOPE_SCHEMA,
            prompt_version=PROMPT_VERSIONS["candidate_scope"],
        )
    except ModelBudgetExceeded:
        raise
    else:
        for entry in response.data.get("candidates") or ():
            regulation = str(entry.get("regulation") or "").strip()
            if regulation not in by_regulation:
                ctx.warn(
                    f"screening returned {regulation!r}, which is not in the supplied "
                    "index; ignored",
                    stage="applicability",
                )
                continue
            results[regulation] = (
                entry.get("verdict") == "candidate",
                str(entry.get("reason") or ""),
                tuple(entry.get("depends_on_unknowns") or ()),
            )

    missing = [r for r in by_regulation if r not in results]
    for regulation in missing:
        results[regulation] = (
            True,
            "Not addressed by index screening; kept as a candidate so its scope text "
            "is read rather than dropped without a reason.",
            (),
        )
    if missing:
        ctx.warn(
            f"{len(missing)} regulation(s) were not addressed by index screening and "
            "were kept as candidates: " + ", ".join(sorted(missing)),
            stage="applicability",
        )
    return results


def decide_document_scope(
    ctx: RunContext,
    profile: ProductProfile,
    regulation: str,
    documents: list[Document],
    index_reason: str,
    index_unknowns: tuple[str, ...],
) -> ApplicabilityDecision:
    """Read one regulation's own scope text and record a decision with evidence."""
    passages: list[SourcePassage] = []
    for document in documents:
        passages.extend(ctx.corpus.read_scope(document.document_id))

    document_ids = tuple(d.document_id for d in documents)
    version_label = documents[0].version_label

    if not passages:
        return ApplicabilityDecision(
            regulation=regulation,
            version_label=version_label,
            document_ids=document_ids,
            status=ApplicabilityStatus.UNCERTAIN,
            explanation=(
                "No scope or definition passages were available from the corpus for "
                "this regulation, so its applicability could not be decided from "
                "source text."
            ),
            depends_on_unknowns=index_unknowns,
            index_evidence=index_reason,
        )

    truncated = len(passages) > MAX_SCOPE_PASSAGES
    if truncated:
        ctx.warn(
            f"{regulation}: scope text exceeded {MAX_SCOPE_PASSAGES} passages and was "
            "truncated for the scope decision",
            stage="applicability",
        )
        passages = passages[:MAX_SCOPE_PASSAGES]

    valid_ids = {p.passage_id for p in passages}
    response = ctx.call_json(
        "document_scope",
        DOCUMENT_SCOPE_INSTRUCTIONS,
        {
            "product": profile_payload(profile),
            "regulation": regulation,
            "version_label": version_label,
            "titles": sorted({d.title for d in documents}),
            "index_screening_reason": index_reason,
            "scope_and_definition_passages": _passage_payload(passages),
            "scope_text_truncated": truncated,
        },
        DOCUMENT_SCOPE_SCHEMA,
        prompt_version=PROMPT_VERSIONS["document_scope"],
    )
    data = response.data

    evidence = tuple(pid for pid in data.get("scope_evidence") or () if pid in valid_ids)
    dropped = [pid for pid in data.get("scope_evidence") or () if pid not in valid_ids]
    if dropped:
        ctx.warn(
            f"{regulation}: scope decision cited {len(dropped)} passage id(s) that were "
            "not supplied; they were dropped from the evidence",
            stage="applicability",
        )

    status = ApplicabilityStatus(data["status"])
    unknowns = tuple(dict.fromkeys((*index_unknowns, *(data.get("depends_on_unknowns") or ()))))

    if status is ApplicabilityStatus.EXCLUDED and not evidence:
        # Exclusion is the only verdict that removes a regulation from the run,
        # so it is the one verdict that must not rest on unverifiable evidence.
        ctx.warn(
            f"{regulation}: exclusion cited no supplied scope passage; recorded as "
            "uncertain so the regulation stays under inspection",
            stage="applicability",
        )
        status = ApplicabilityStatus.UNCERTAIN

    explanation = str(data.get("explanation") or "")
    if data.get("notes"):
        explanation = f"{explanation} Note: {data['notes']}"

    return ApplicabilityDecision(
        regulation=regulation,
        version_label=version_label,
        document_ids=document_ids,
        status=status,
        explanation=explanation.strip(),
        scope_evidence=evidence,
        conditions=tuple(data.get("conditions") or ()),
        depends_on_unknowns=unknowns,
        index_evidence=index_reason,
    )


def assess_applicability(
    ctx: RunContext, profile: ProductProfile
) -> tuple[list[ApplicabilityDecision], list[Document]]:
    """Return every decision plus the documents selected for extraction.

    Base versions only: the main goal cites the exact base versions in the
    participant index.
    """
    documents = list(ctx.corpus.list_documents(DocumentRole.BASE))
    if not documents:
        ctx.warn("the corpus snapshot contains no base-role documents", stage="applicability")
        return [], []

    shortlist = shortlist_candidates(ctx, profile, documents)
    by_regulation = _group_by_regulation(documents)

    decisions: list[ApplicabilityDecision] = []
    total = len(by_regulation)
    for position, (regulation, docs) in enumerate(sorted(by_regulation.items()), start=1):
        is_candidate, reason, unknowns = shortlist.get(regulation, (True, "", ()))
        if not is_candidate:
            decisions.append(
                ApplicabilityDecision(
                    regulation=regulation,
                    version_label=docs[0].version_label,
                    document_ids=tuple(d.document_id for d in docs),
                    status=ApplicabilityStatus.EXCLUDED,
                    explanation=(
                        "Excluded at index screening without reading the scope text: "
                        + reason
                    ),
                    depends_on_unknowns=unknowns,
                    index_evidence=reason,
                )
            )
            ctx.event(
                "applicability",
                f"{regulation}: not a candidate",
                completed=position,
                total=total,
            )
            continue

        try:
            decision = decide_document_scope(
                ctx, profile, regulation, docs, reason, unknowns
            )
        except ModelBudgetExceeded:
            ctx.warn(
                f"model budget exhausted before {regulation}'s scope was read; it is "
                "recorded as uncertain and left uninspected",
                stage="applicability",
            )
            decisions.append(
                ApplicabilityDecision(
                    regulation=regulation,
                    version_label=docs[0].version_label,
                    document_ids=tuple(d.document_id for d in docs),
                    status=ApplicabilityStatus.UNCERTAIN,
                    explanation="Scope not read: the run's model budget was exhausted.",
                    depends_on_unknowns=unknowns,
                    index_evidence=reason,
                )
            )
            break
        decisions.append(decision)
        ctx.event(
            "applicability",
            f"{regulation}: {decision.status.value}",
            completed=position,
            total=total,
            affected_ids=decision.scope_evidence[:5],
        )

    screened_out = [
        d.regulation
        for d in decisions
        if d.status is ApplicabilityStatus.EXCLUDED and not d.scope_evidence
    ]
    if screened_out:
        # Stage 1 can exclude a regulation without reading its scope text. That
        # is a deliberate cost tradeoff, but it is a weaker basis than a
        # scope-text decision, so the run states it rather than burying it.
        ctx.warn(
            f"{len(screened_out)} regulation(s) were excluded at index screening "
            "without reading their scope text: " + ", ".join(sorted(screened_out)),
            stage="applicability",
        )

    selected = select_documents(ctx, decisions, documents)
    return decisions, selected


def select_documents(
    ctx: RunContext, decisions: list[ApplicabilityDecision], documents: list[Document]
) -> list[Document]:
    """Documents to inspect: applicable, plus uncertain when configured.

    Keeping uncertain regulations is the default. Dropping them would convert
    "we could not establish a product fact" into "this regulation does not
    apply", which is a different and unsupported claim.
    """
    by_id = {d.document_id: d for d in documents}
    keep_statuses = {ApplicabilityStatus.APPLICABLE}
    if ctx.config.include_uncertain_documents:
        keep_statuses.add(ApplicabilityStatus.UNCERTAIN)

    selected: list[Document] = []
    for decision in decisions:
        if decision.status not in keep_statuses:
            continue
        for document_id in decision.document_ids:
            document = by_id.get(document_id)
            if document is not None and document not in selected:
                selected.append(document)

    limit = ctx.config.max_documents
    if limit is not None and len(selected) > limit:
        ctx.warn(
            f"RunConfig.max_documents={limit} limited inspection to the first "
            f"{limit} of {len(selected)} selected documents; coverage is partial",
            stage="applicability",
        )
        selected = selected[:limit]
    return selected
