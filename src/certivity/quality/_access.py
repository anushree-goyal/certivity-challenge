"""Single place where this module reads fields from shared contract records.

Anushree's src/certivity/contracts.py was not available when this was written, so
the attribute names below are ASSUMED from the build plan's record table. When the
contracts are published, adjust the name tuples here and nowhere else.
Records may be dataclasses, pydantic models or plain dicts.
"""
from __future__ import annotations

from typing import Any


def pick(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
        elif hasattr(obj, name):
            return getattr(obj, name)
    return default


# --- PipelineDraft / DraftRequirement ---
def drafts_of(pipeline_draft: Any) -> list:
    return list(pick(pipeline_draft, "draft_requirements", "requirements", default=[]) or [])


def draft_id(d: Any) -> str | None:
    return pick(d, "draft_id", "id")


def draft_text(d: Any) -> str:
    return pick(d, "text", "obligation_text", default="") or ""


def draft_supporting_ids(d: Any) -> list[str]:
    return list(pick(d, "supporting_passage_ids", "supporting_passages", default=[]) or [])


def draft_primary_id(d: Any) -> str | None:
    return pick(d, "primary_passage_id", "primary_passage")


def draft_condition(d: Any) -> str | None:
    return pick(d, "applicability_condition", "condition")


# --- SourcePassage ---
def passage_id(p: Any) -> str | None:
    return pick(p, "passage_id", "id")


def passage_document_id(p: Any) -> str | None:
    return pick(p, "document_id")


def passage_text(p: Any) -> str:
    return pick(p, "text", default="") or ""


def passage_paragraph(p: Any) -> str | None:
    return pick(p, "paragraph_label", "paragraph")


def passage_printed_page(p: Any) -> Any:
    return pick(p, "printed_start_page", "printed_page", "start_page")


def passage_spans_pages(p: Any) -> bool:
    span = pick(p, "page_span")
    if isinstance(span, (list, tuple)):
        return len(span) >= 2 and span[0] != span[-1]
    if isinstance(span, int) and not isinstance(span, bool):
        return span > 1
    return False


# --- Document ---
def doc_id(d: Any) -> str | None:
    return pick(d, "document_id", "id")


def doc_regulation(d: Any) -> str | None:
    return pick(d, "regulation")


def doc_version(d: Any) -> str | None:
    return pick(d, "version_label", "version")


def doc_snapshot(d: Any) -> str | None:
    return pick(d, "snapshot_id")


# --- RunInput ---
def run_team_id(r: Any) -> str | None:
    return pick(r, "team_id")


def run_case_id(r: Any) -> str | None:
    return pick(r, "case_id")


def run_snapshot_id(r: Any) -> str | None:
    return pick(r, "corpus_snapshot_id", "snapshot_id")


# --- additions for semantic review / vision (names ASSUMED, see module docstring) ---
def passage_flags(p: Any) -> list:
    return list(pick(p, "parsing_flags", "flags", default=[]) or [])


def passage_pdf_page_indices(p: Any) -> list:
    return list(pick(p, "pdf_page_indices", "physical_page_indices", default=[]) or [])


def passage_heading(p: Any) -> list | str | None:
    return pick(p, "heading_ancestry", "headings")


def llm_data(resp: Any) -> Any:
    """LLMResponse.parsed structured data."""
    return pick(resp, "parsed", "data")
