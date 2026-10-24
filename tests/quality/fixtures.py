"""SYNTHETIC FIXTURES - invented text, not real regulation content.

Factories build real certivity.contracts records (1.5.0) so the tests exercise the shared shapes.
"""
import dataclasses
from typing import Any

from certivity.contracts import (Document, DocumentRole, DraftRequirement, LLMResponse,
                                 PipelineDraft, ProductProfile, RunInput, SourcePassage)

SNAP = "snap-1"
RUN_ID = "run-test"


def Doc(document_id, regulation, version_label, role="base", snapshot_id=SNAP):
    return Document(document_id=document_id, regulation=regulation, title=f"{regulation} (synthetic)",
                    role=DocumentRole(role), version_label=version_label,
                    filename=f"{document_id}.pdf", checksum="0" * 64, snapshot_id=snapshot_id,
                    page_count=100)


def Passage(passage_id, document_id, text, paragraph_label="5.1", printed_start_page=12,
            page_span=None, **kw):
    page = None if printed_start_page is None else str(printed_start_page)
    span = tuple(str(x) for x in (page_span or ((printed_start_page,) if page else ())))
    return SourcePassage(passage_id=passage_id, document_id=document_id,
                         section_id=f"{document_id}#main/{paragraph_label or 'x'}", text=text,
                         paragraph_label=paragraph_label, heading_ancestry=(), printed_start_page=page,
                         page_span=span, **kw)


def Draft(draft_id, text, primary_passage_id, supporting_passage_ids=(), **kw):
    support = tuple(supporting_passage_ids)
    if primary_passage_id and primary_passage_id not in support:  # the contract requires it
        support = (primary_passage_id,) + support
    return DraftRequirement(draft_id=draft_id, text=text, supporting_passage_ids=support,
                            primary_passage_id=primary_passage_id or "", **kw)


def Pipeline(drafts):
    return PipelineDraft(run_id=RUN_ID, corpus_snapshot_id=SNAP, product_profile=ProductProfile(),
                         draft_requirements=tuple(drafts), is_synthetic_fixture=True)


def Run(team_id="team-x", case_id="case-y", corpus_snapshot_id=SNAP):
    return RunInput(run_id=RUN_ID, team_id=team_id, case_id=case_id,
                    corpus_snapshot_id=corpus_snapshot_id, input_hash="h",
                    specification_text="synthetic rear lamp")


def with_accepted(report, ids):
    return dataclasses.replace(report, accepted_draft_ids=tuple(ids))


class FakeCorpus:
    def __init__(self, docs, passages):
        self.docs = docs
        self.passages = {p.passage_id: p for p in passages}

    @property
    def snapshot_id(self):
        return SNAP

    def list_documents(self, role="base"):
        return [d for d in self.docs if d.role == DocumentRole(role)]

    def get_passages(self, ids):
        return [self.passages[i] for i in ids if i in self.passages]


BASE = Doc("doc-r999-base", "UN R999", "01 series")
LATEST = Doc("doc-r999-latest", "UN R999", "02 series", role="latest")

P_OK = Passage("p-ok", BASE.document_id,
               "The luminous intensity shall be at least 4 cd and at most 600 cd.", "6.2.1", 12)
P_LATEST = Passage("p-latest", LATEST.document_id, "The intensity shall be at least 5 cd.", "6.2.1", 13)
P_EXC = Passage("p-exc", BASE.document_id,
                "Lamps shall be red, unless the lamp is combined with a stop lamp.", "6.3", 14)
P_NOPAGE = Passage("p-nopage", BASE.document_id, "Marking shall be indelible.", "7.1", None)
P_SPAN = Passage("p-span", BASE.document_id, "Long paragraph.", "8", 20, (20, 21))


def corpus():
    return FakeCorpus([BASE, LATEST], [P_OK, P_LATEST, P_EXC, P_NOPAGE, P_SPAN])


class FakeLLM:
    """Scripted stand-in for the shared adapter. `script` maps task -> callable(payload)->dict."""
    def __init__(self, script):
        self.script, self.calls = script, []

    def generate_json(self, task, instructions, payload, schema, images=None, **kwargs):
        self.calls.append({"task": task, "payload": payload, "images": images, **kwargs})
        out = self.script[task](payload)
        if isinstance(out, Exception):
            raise out
        return LLMResponse(data=out, provider="fake", model="fake", task=task, latency_ms=0.0)


def ok_review(did, quote="at least 4 cd", **over):
    r = {"draft_id": did, "verdict": "supported", "explanation": "matches",
         "checks": {c: "ok" for c in ("subject", "scope", "value", "unit", "condition", "exception")},
         "evidence_quote": quote}
    r.update(over)
    return r
