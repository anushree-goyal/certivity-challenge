"""Product interpretation: specification text and drawings to ProductProfile.

Owner: Anushree. The profile is the basis of every later scope decision, so
its ``unknowns`` list matters as much as its facts: an unknown keeps a
regulation in the analysis, whereas a confidently wrong fact removes it.
"""

from __future__ import annotations

from typing import Any, Optional

from certivity.contracts import (
    EvidenceSpan,
    ImagePayload,
    ProductFact,
    ProductProfile,
    RunInput,
)
from certivity.agent.context import RunContext
from certivity.agent.prompts import (
    PRODUCT_PROFILE_INSTRUCTIONS,
    PRODUCT_PROFILE_SCHEMA,
    PROMPT_VERSIONS,
)

_VISUAL_ONLY_BASES = {"observed"}
_UNVERIFIABLE_FROM_DRAWING = (
    "material",
    "colour_standard",
    "color_standard",
    "luminous",
    "photometric",
    "candela",
    "voltage",
    "approval",
    "test_result",
    "temperature",
    "ingress",
)


def _facts(entries: Any, group: str, ctx: RunContext) -> tuple[ProductFact, ...]:
    facts: list[ProductFact] = []
    for entry in entries or ():
        evidence = tuple(
            EvidenceSpan(
                source=str(span.get("source") or "specification"),
                quote=span.get("quote"),
                location=span.get("location"),
            )
            for span in entry.get("evidence") or ()
        )
        fact = ProductFact(
            key=str(entry["key"]),
            value=entry.get("value"),
            basis=str(entry.get("basis") or "unknown"),
            evidence=evidence,
            notes=entry.get("notes"),
        )
        facts.append(_demote_unverifiable(fact, group, ctx))
    return tuple(facts)


def _demote_unverifiable(fact: ProductFact, group: str, ctx: RunContext) -> ProductFact:
    """Guard the rule that a drawing's appearance does not prove a specification.

    The prompt states this, but a prompt is not an enforcement mechanism. A fact
    claimed as directly observed from a drawing while naming a measured or
    certified property is downgraded to ``inferred`` and flagged, so a later
    scope decision cannot rest on it as if the input had stated it.
    """
    if fact.basis not in _VISUAL_ONLY_BASES:
        return fact
    drawing_only = bool(fact.evidence) and all(
        span.source != "specification" for span in fact.evidence
    )
    if not drawing_only:
        return fact
    haystack = f"{fact.key} {fact.value or ''}".lower()
    if not any(token in haystack for token in _UNVERIFIABLE_FROM_DRAWING):
        return fact
    ctx.warn(
        f"product fact {fact.key!r} ({group}) was claimed as observed from a drawing "
        "but names a measured or certified property; recorded as inferred",
        stage="product_profile",
    )
    note = "Downgraded from 'observed': a drawing cannot establish this property."
    return ProductFact(
        key=fact.key,
        value=fact.value,
        basis="inferred",
        evidence=fact.evidence,
        notes=f"{fact.notes} {note}".strip() if fact.notes else note,
    )


def _images(run_input: RunInput) -> list[ImagePayload]:
    return [
        ImagePayload(
            media_type=drawing.media_type,
            asset_ref=drawing.asset_ref,
            caption=drawing.caption or drawing.drawing_id,
        )
        for drawing in run_input.drawings
    ]


def build_product_profile(ctx: RunContext) -> ProductProfile:
    """Derive the profile from whatever the case actually supplied."""
    run_input = ctx.run_input
    ctx.event("product_profile", "Reading the product input")

    payload: dict[str, Any] = {
        "case_id": run_input.case_id,
        "specification_text": run_input.specification_text,
        "drawings": [
            {"drawing_id": d.drawing_id, "caption": d.caption} for d in run_input.drawings
        ],
    }
    if not run_input.specification_text:
        payload["note"] = (
            "No specification text was supplied. Every fact must come from the "
            "drawings, and properties a drawing cannot establish are unknowns."
        )

    response = ctx.call_json(
        "product_profile",
        PRODUCT_PROFILE_INSTRUCTIONS,
        payload,
        PRODUCT_PROFILE_SCHEMA,
        prompt_version=PROMPT_VERSIONS["product_profile"],
        images=_images(run_input),
    )
    data = response.data

    profile = ProductProfile(
        product_type=data.get("product_type"),
        vehicle_categories=tuple(data.get("vehicle_categories") or ()),
        facts=_facts(data.get("facts"), "facts", ctx),
        components=_facts(data.get("components"), "components", ctx),
        functions=_facts(data.get("functions"), "functions", ctx),
        materials=_facts(data.get("materials"), "materials", ctx),
        unknowns=tuple(data.get("unknowns") or ()),
        summary=data.get("summary"),
    )
    profile = _promote_valueless_facts(profile)

    if not profile.unknowns:
        ctx.warn(
            "the product profile lists no unknowns; scope decisions will have no "
            "recorded uncertainty to carry forward",
            stage="product_profile",
        )
    ctx.event(
        "product_profile",
        f"Profile built: {len(profile.fact_keys())} facts, "
        f"{len(profile.unknowns)} unknowns",
        affected_ids=profile.unknowns[:10],
    )
    return profile


def _promote_valueless_facts(profile: ProductProfile) -> ProductProfile:
    """A named fact with no value is an unknown, whatever list it arrived in.

    Without this, a fact recorded as ``{"key": "vehicle_category", "value": null}``
    would never reach ``unknowns``, and the applicability step would lose the
    one signal that keeps a regulation under inspection.
    """
    unknowns = list(profile.unknowns)
    for group in (profile.facts, profile.components, profile.functions, profile.materials):
        for fact in group:
            if fact.value is None or fact.basis == "unknown":
                if fact.key not in unknowns:
                    unknowns.append(fact.key)
    if len(unknowns) == len(profile.unknowns):
        return profile
    return ProductProfile(
        product_type=profile.product_type,
        vehicle_categories=profile.vehicle_categories,
        facts=profile.facts,
        components=profile.components,
        functions=profile.functions,
        materials=profile.materials,
        unknowns=tuple(unknowns),
        summary=profile.summary,
    )


def profile_payload(profile: ProductProfile) -> dict[str, Any]:
    """Compact profile view embedded in later prompts.

    Later stages need the facts and - critically - the unknowns, not the full
    evidence spans, which would repeat in every section call.
    """

    def facts(group: tuple[ProductFact, ...]) -> list[dict[str, Optional[str]]]:
        return [{"key": f.key, "value": f.value, "basis": f.basis} for f in group]

    return {
        "product_type": profile.product_type,
        "vehicle_categories": list(profile.vehicle_categories),
        "summary": profile.summary,
        "facts": facts(profile.facts),
        "components": facts(profile.components),
        "functions": facts(profile.functions),
        "materials": facts(profile.materials),
        "unknown_product_facts": list(profile.unknowns),
    }
