"""SYNTHETIC FIXTURES - invented text, not real regulation content.

Field names mirror the build plan's record table and are ASSUMPTIONS until
certivity.contracts is published (see src/certivity/quality/_access.py).
"""
from dataclasses import dataclass, field
from typing import Any

SNAP = "snap-1"


@dataclass
class Doc:
    document_id: str
    regulation: str
    version_label: str
    role: str = "base"
    snapshot_id: str = SNAP


@dataclass
class Passage:
    passage_id: str
    document_id: str
    text: str
    paragraph_label: Any = "5.1"
    printed_start_page: Any = 12
    page_span: Any = (12, 12)


@dataclass
class Draft:
    draft_id: str
    text: str
    primary_passage_id: Any
    supporting_passage_ids: list = field(default_factory=list)
    applicability_condition: Any = None


@dataclass
class Pipeline:
    draft_requirements: list


@dataclass
class Run:
    team_id: str = "team-x"
    case_id: str = "case-y"
    corpus_snapshot_id: str = SNAP


class FakeCorpus:
    def __init__(self, docs, passages):
        self.docs = docs
        self.passages = {p.passage_id: p for p in passages}

    def list_documents(self, role="base"):
        return [d for d in self.docs if d.role == role]

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

    def generate_json(self, task, instructions, payload, schema, images=None):
        self.calls.append({"task": task, "payload": payload, "images": images})
        out = self.script[task](payload)
        if isinstance(out, Exception):
            raise out
        return type("Resp", (), {"parsed": out})()


def ok_review(did, quote="at least 4 cd", **over):
    r = {"draft_id": did, "verdict": "supported", "explanation": "matches",
         "checks": {c: "ok" for c in ("subject", "scope", "value", "unit", "condition", "exception")},
         "evidence_quote": quote}
    r.update(over)
    return r
