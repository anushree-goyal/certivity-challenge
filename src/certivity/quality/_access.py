"""Single place where this module reads fields from shared contract records (contracts 1.5.0).

Kept as small accessors so a contract change is absorbed here rather than across the module.
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
    return list(pipeline_draft.draft_requirements)


def draft_id(d: Any) -> str | None:
    return d.draft_id


def draft_text(d: Any) -> str:
    return d.text or ""


def draft_supporting_ids(d: Any) -> list[str]:
    return list(d.supporting_passage_ids)


def draft_primary_id(d: Any) -> str | None:
    return d.primary_passage_id or None


def draft_condition(d: Any) -> str | None:
    return d.applicability_condition


def draft_exceptions(d: Any) -> list[str]:
    return list(d.exceptions)


def draft_section_id(d: Any) -> str | None:
    return d.section_id


def draft_unresolved_references(d: Any) -> list:
    return list(d.unresolved_references)


def draft_notes(d: Any) -> str:
    return d.notes or ""


# --- CorpusReader ---
def get_passages(corpus: Any, ids) -> list:
    """Passages for ``ids``, omitting unknown ones (the contract's rule; feature/corpus d76052b).

    Defensive: if a reader raises anyway, fall back to one lookup per ID so one bad citation
    cannot abort the whole review.
    """
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return []
    try:
        return list(corpus.get_passages(ids))
    except Exception:  # noqa: BLE001
        out = []
        for i in ids:
            try:
                out += list(corpus.get_passages([i]))
            except Exception:  # noqa: BLE001
                pass
        return out


# --- SourcePassage ---
def passage_id(p: Any) -> str | None:
    return p.passage_id


def passage_document_id(p: Any) -> str | None:
    return p.document_id


def passage_section_id(p: Any) -> str | None:
    return p.section_id


def passage_text(p: Any) -> str:
    return p.text or ""


def passage_paragraph(p: Any) -> str | None:
    return p.paragraph_label


def passage_namespace(p: Any) -> str:
    return p.namespace


def passage_printed_page(p: Any) -> Any:
    """Printed label (string) of the first page, or None when unverified."""
    return p.printed_start_page


def passage_spans_pages(p: Any) -> bool:
    return len(set(p.page_span)) > 1 or "spans_pages" in p.parsing_flags


def passage_flags(p: Any) -> list:
    return list(p.parsing_flags)


def passage_is_table(p: Any) -> bool:
    return bool(p.is_table)


def passage_table_context(p: Any) -> str | None:
    return p.table_context


def passage_pdf_page_indices(p: Any) -> list:
    return list(p.pdf_page_indices)


def passage_heading(p: Any) -> list:
    return list(p.heading_ancestry)


# --- Document ---
def doc_id(d: Any) -> str | None:
    return d.document_id


def doc_regulation(d: Any) -> str | None:
    return d.regulation


def doc_version(d: Any) -> str | None:
    return d.version_label


def doc_snapshot(d: Any) -> str | None:
    return d.snapshot_id


# --- RunInput ---
def run_id(r: Any) -> str:
    return r.run_id


def run_team_id(r: Any) -> str | None:
    return r.team_id


def run_case_id(r: Any) -> str | None:
    return r.case_id


def run_snapshot_id(r: Any) -> str | None:
    return r.corpus_snapshot_id


# --- LLMResponse ---
def llm_data(resp: Any) -> Any:
    return resp.data
