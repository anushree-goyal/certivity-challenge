"""FIXTURE MODE: labelled stand-ins for the corpus, agent and quality components.

Exists only so the run API, worker and UI can be built and tested before the real modules are
merged. Every passage, page and requirement here is synthetic and carries "[FIXTURE]" in its text.
Replace with Anushree's shared contract fixtures (tests/fixtures/contracts/) once published.

The record shapes follow the field lists in the shared plan; certivity.contracts is canonical.
"""

from __future__ import annotations

import html
import importlib.util
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from certivity.api.settings import Settings

FIXTURE_VERSION = "fixture-1"
SNAPSHOT_ID = "fixture-snapshot"

_DOCS = [
    {"document_id": "fixture:UN_R48:9.3.0", "regulation": "UN R48", "title": "[FIXTURE] UN R48",
     "role": "base", "version_label": "9.3.0", "filename": "UN_R48_base.pdf", "checksum": "fixture",
     "snapshot_id": SNAPSHOT_ID, "page_count": 3},
    {"document_id": "fixture:UN_R148:1.6.0", "regulation": "UN R148", "title": "[FIXTURE] UN R148",
     "role": "base", "version_label": "1.6.0", "filename": "UN_R148_base.pdf", "checksum": "fixture",
     "snapshot_id": SNAPSHOT_ID, "page_count": 3},
]


def _passage(doc: str, section: str, label: str, text: str, printed: int, pdf_index: int) -> dict[str, Any]:
    return {
        "passage_id": f"{doc}#{label}", "document_id": doc, "section_id": f"{doc}#s{section}",
        "text": f"[FIXTURE] {text}", "paragraph_label": label, "heading_ancestry": [f"Section {section}"],
        "printed_start_page": printed, "page_span": [printed, printed], "pdf_page_indices": [pdf_index],
        "parsing_flags": ["fixture"],
    }


_PASSAGES = {p["passage_id"]: p for p in [
    _passage("fixture:UN_R48:9.3.0", "6.7", "6.7.1", "Stop lamps: presence is mandatory on vehicles of category M1.", 6, 7),
    _passage("fixture:UN_R48:9.3.0", "6.7", "6.7.4.1", "Stop lamps: at least two devices, symmetric about the median plane.", 6, 7),
    _passage("fixture:UN_R48:9.3.0", "6.4", "6.4.1", "Reversing lamp: one device mandatory; a second is optional.", 5, 6),
    _passage("fixture:UN_R148:1.6.0", "5", "5.2", "The device shall bear the trade name or mark of the applicant.", 4, 4),
    _passage("fixture:UN_R148:1.6.0", "Annex 4", "Annex 4 2.1", "Colour of light emitted by the stop lamp: red.", 9, 9),
]}


class FixtureCorpus:
    """In-memory CorpusReader stand-in. Pages are SVG placeholders, not real regulation pages."""

    snapshot_id = SNAPSHOT_ID

    def list_documents(self, role: str = "base") -> list[dict[str, Any]]:
        return [d for d in _DOCS if d["role"] == role]

    def get_passages(self, passage_ids: list[str]) -> list[dict[str, Any]]:
        return [_PASSAGES[p] for p in passage_ids if p in _PASSAGES]

    def get_document_path(self, document_id: str) -> None:
        return None  # fixture documents have no PDF

    def get_page_asset(self, document_id: str, pdf_page_index: int) -> dict[str, Any]:
        doc = next((d for d in _DOCS if d["document_id"] == document_id), None)
        if doc is None:
            raise KeyError(document_id)
        on_page = [p for p in _PASSAGES.values() if p["document_id"] == document_id and pdf_page_index in p["pdf_page_indices"]]
        printed = on_page[0]["printed_start_page"] if on_page else None
        lines = "".join(
            f'<text x="40" y="{200 + i * 40}" font-size="18">{html.escape(p["paragraph_label"] + "  " + p["text"])}</text>'
            for i, p in enumerate(on_page)
        )
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="850" height="1100" font-family="sans-serif">'
            '<rect width="100%" height="100%" fill="#fff8e1"/>'
            '<text x="40" y="70" font-size="30" fill="#b45309">FIXTURE PAGE: not a real regulation page</text>'
            f'<text x="40" y="120" font-size="20">{html.escape(doc["regulation"])} {doc["version_label"]} | '
            f'PDF page index {pdf_page_index} | printed page {printed if printed else "unknown"}</text>'
            f"{lines}</svg>"
        )
        return {"document_id": document_id, "pdf_page_index": pdf_page_index,
                "printed_page_label": str(printed) if printed else None,
                "media_type": "image/svg+xml", "content": svg.encode("utf-8")}


class FixtureLLM:
    identity = "fixture (no model calls)"


def _drafts(revised: bool) -> list[dict[str, Any]]:
    stop = "fixture:UN_R48:9.3.0#6.7.1"
    drafts = [
        {"draft_id": "D-1", "text": "[FIXTURE] An M1 vehicle shall be fitted with stop lamps.",
         "supporting_passage_ids": [stop], "primary_passage_id": stop,
         "applicability_condition": "Vehicle category M1", "applicability_reason": "Spec states category M1.",
         "unresolved_references": []},
        {"draft_id": "D-2", "text": "[FIXTURE] At least two stop lamps shall be fitted, symmetric about the median longitudinal plane.",
         "supporting_passage_ids": ["fixture:UN_R48:9.3.0#6.7.4.1"], "primary_passage_id": "fixture:UN_R48:9.3.0#6.7.4.1",
         "applicability_condition": None, "applicability_reason": "Product includes a stop lamp.", "unresolved_references": []},
        {"draft_id": "D-3", "text": "[FIXTURE] At least two stop lamps shall be fitted symmetrically.",
         "supporting_passage_ids": ["fixture:UN_R48:9.3.0#6.7.4.1"], "primary_passage_id": "fixture:UN_R48:9.3.0#6.7.4.1",
         "applicability_condition": None, "applicability_reason": "Duplicate drafted on purpose.", "unresolved_references": []},
        {"draft_id": "D-4", "text": "[FIXTURE] One reversing lamp shall be fitted; a second reversing lamp is optional.",
         "supporting_passage_ids": ["fixture:UN_R48:9.3.0#6.4.1"], "primary_passage_id": "fixture:UN_R48:9.3.0#6.4.1",
         "applicability_condition": None, "applicability_reason": "Product includes a reversing lamp.", "unresolved_references": []},
        {"draft_id": "D-5", "text": "[FIXTURE] The device shall bear the trade name or mark of the applicant.",
         "supporting_passage_ids": ["fixture:UN_R148:1.6.0#5.2"], "primary_passage_id": "fixture:UN_R148:1.6.0#5.2",
         "applicability_condition": None, "applicability_reason": "Lamp is a type-approved device.", "unresolved_references": []},
        {"draft_id": "D-6",
         "text": "[FIXTURE] The stop lamp shall emit red light." if revised else "[FIXTURE] The stop lamp shall emit light of the colour defined in Annex 4.",
         "supporting_passage_ids": ["fixture:UN_R148:1.6.0#Annex 4 2.1"], "primary_passage_id": "fixture:UN_R148:1.6.0#Annex 4 2.1",
         "applicability_condition": None, "applicability_reason": "Product includes a stop lamp.",
         "unresolved_references": [] if revised else [{"raw_reference": "Annex 4", "status": "pending"}]},
    ]
    return drafts


def _draft(revised: bool) -> dict[str, Any]:
    return {
        "product_profile": {
            "facts": [
                {"name": "product_type", "value": "[FIXTURE] Rear lamp", "evidence": "spec.md: Product Type"},
                {"name": "vehicle_category", "value": "M1", "evidence": "spec.md: Vehicle Category"},
            ],
            "components": ["Stop lamp", "Reversing lamp", "Rear position lamp"],
            "functions": ["Braking indication", "Reversing illumination"],
            "materials": [{"component": "Outer lens", "material": "PMMA", "evidence": "spec.md: Materials (representative)"}],
            "unknowns": ["Measured photometric intensity", "Exact mounting height"],
        },
        "applicability_decisions": [
            {"regulation": "UN R48", "version_label": "9.3.0", "document_ids": ["fixture:UN_R48:9.3.0"], "status": "applicable",
             "scope_evidence": ["fixture:UN_R48:9.3.0#6.7.1"], "conditions": [], "explanation": "[FIXTURE] Installation of lighting on M1."},
            {"regulation": "UN R148", "version_label": "1.6.0", "document_ids": ["fixture:UN_R148:1.6.0"], "status": "applicable",
             "scope_evidence": ["fixture:UN_R148:1.6.0#5.2"], "conditions": [], "explanation": "[FIXTURE] Signalling device approval."},
            {"regulation": "UN R10", "version_label": "7.2.0", "document_ids": [], "status": "uncertain",
             "scope_evidence": [], "conditions": ["Electronic sub-assembly present"], "explanation": "[FIXTURE] LED modules may be ESAs."},
            {"regulation": "UN R13H", "version_label": None, "document_ids": [], "status": "excluded",
             "scope_evidence": [], "conditions": [], "explanation": "[FIXTURE] Braking systems, not lamps."},
        ],
        "draft_requirements": _drafts(revised),
        "coverage": [
            {"document_id": "fixture:UN_R48:9.3.0", "section_id": "fixture:UN_R48:9.3.0#s6.7", "status": "processed",
             "explanation": "Stop lamp installation", "inspected_passage_ids": ["fixture:UN_R48:9.3.0#6.7.1", "fixture:UN_R48:9.3.0#6.7.4.1"]},
            {"document_id": "fixture:UN_R48:9.3.0", "section_id": "fixture:UN_R48:9.3.0#s6.4", "status": "processed",
             "explanation": "Reversing lamp installation", "inspected_passage_ids": ["fixture:UN_R48:9.3.0#6.4.1"]},
            {"document_id": "fixture:UN_R148:1.6.0", "section_id": "fixture:UN_R148:1.6.0#s5", "status": "processed",
             "explanation": "Markings", "inspected_passage_ids": ["fixture:UN_R148:1.6.0#5.2"]},
            {"document_id": "fixture:UN_R148:1.6.0", "section_id": "fixture:UN_R148:1.6.0#sAnnex 4", "status": "processed",
             "explanation": "Colours", "inspected_passage_ids": ["fixture:UN_R148:1.6.0#Annex 4 2.1"]},
            {"document_id": "fixture:UN_R148:1.6.0", "section_id": "fixture:UN_R148:1.6.0#sAnnex 5", "status": "pending",
             "explanation": "[FIXTURE] Deliberately left pending to show partial coverage.", "inspected_passage_ids": []},
        ],
        "unresolved_references": [],
        "warnings": ["Fixture pipeline: synthetic data, no model calls."],
        "usage": {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "revised": revised},
    }


def run_extraction(run_input, corpus, llm, emit_event):
    for i, stage_msg in enumerate(["Building product profile", "Assessing applicability", "Reading sections"], 1):
        emit_event({"stage": "extraction", "message": f"[fixture] {stage_msg}", "completed": i, "total": 3})
        time.sleep(0.2)
    return _draft(revised=False)


def revise_extraction(run_input, draft, findings, corpus, llm, emit_event):
    emit_event({"stage": "revision", "message": f"[fixture] Revising {len(findings)} findings", "completed": 1, "total": 1})
    return _draft(revised=True)


def review_requirements(run_input, draft, corpus, llm, emit_event):
    emit_event({"stage": "review", "message": "[fixture] Reviewing drafts", "completed": 1, "total": 1})
    drafts = {d["draft_id"]: d for d in draft["draft_requirements"]}
    revised = "Annex 4" not in drafts["D-6"]["text"]
    findings = [{"draft_id": "D-3", "category": "duplicate", "explanation": "[FIXTURE] Same obligation as D-2.",
                 "supporting_passage_ids": ["fixture:UN_R48:9.3.0#6.7.4.1"], "requested_context": None}]
    accepted = ["D-1", "D-2", "D-4", "D-5"]
    remaining = []
    if revised:
        accepted.append("D-6")
    else:
        findings.append({"draft_id": "D-6", "category": "unresolved_reference",
                         "explanation": "[FIXTURE] Colour depends on Annex 4; resolve and state the colour.",
                         "supporting_passage_ids": ["fixture:UN_R148:1.6.0#Annex 4 2.1"],
                         "requested_context": "Resolve reference to Annex 4 colour definition"})
        remaining.append("D-6 waits for reference resolution")
    return {"accepted_draft_ids": accepted, "findings": findings, "duplicate_mapping": {"D-3": "D-2"},
            "rejected": [{"draft_id": "D-3", "reason": "Duplicate of D-2"}], "remaining_issues": remaining}


def build_submission(run_input, draft, report, corpus):
    drafts = {d["draft_id"]: d for d in draft["draft_requirements"]}
    docs = {d["document_id"]: d for d in _DOCS}
    requirements = []
    for i, draft_id in enumerate(report["accepted_draft_ids"], 1):
        d = drafts[draft_id]
        passage = _PASSAGES[d["primary_passage_id"]]
        doc = docs[passage["document_id"]]
        requirements.append({
            "id": f"R-{i:03d}", "text": d["text"],
            "source": {"regulation": doc["regulation"], "version": doc["version_label"],
                       "page": passage["printed_start_page"], "paragraph": passage["paragraph_label"]},
        })
    return {"team_id": run_input["team_id"], "case_id": run_input["case_id"], "requirements": requirements}


@lru_cache(maxsize=4)
def _official_validator(package: str):
    path = Path(package) / "tools" / "validate.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("participant_validate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_submission(submission, participant_package_path):
    """Calls the official participant validator; same check the platform runs."""
    module = _official_validator(str(participant_package_path))
    if module is None:
        return {"valid": False, "errors": [{"path": "$", "message": f"Official validator not found under {participant_package_path}"}],
                "checked_index": None}
    index_path = Path(participant_package_path) / "regulations" / "index.json"
    index = module.load_index(index_path)
    problems = module.validate(submission, index=index)
    return {"valid": not problems, "errors": [{"path": p.path, "message": p.message} for p in problems],
            "checked_index": str(index_path), "validator": "participant tools/validate.py"}


def components(settings: Settings):
    from certivity.api.components import Components

    return Components(
        mode="fixture",
        open_corpus=FixtureCorpus,
        create_llm=lambda agent_dir: FixtureLLM(),
        run_extraction=run_extraction,
        revise_extraction=revise_extraction,
        review_requirements=review_requirements,
        build_submission=build_submission,
        build_submission_detailed=None,
        validate_submission=validate_submission,
        evaluate_submission=None,  # fixture output is never scored
        make_run_input=dict,
    )
