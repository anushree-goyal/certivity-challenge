"""Offline tests: the real participant package and PDFs, a scripted model. No API calls."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from certivity import checks, corpus, pipeline
from certivity.config import settings

pytestmark = pytest.mark.skipif(not corpus.documents().get("UN R148") or not corpus.documents()["UN R148"].pdf,
                                reason="participant package PDFs not downloaded")


class FakeLLM:
    """Chooses UN R148 and keeps the first paragraph of every batch, plus one invented id."""

    def __init__(self) -> None:
        from certivity.llm import Usage
        self.usage = Usage()

    def json(self, *, model, system, content, schema, effort="low"):
        if "applicable" in schema["properties"]:
            return {"product": {"summary": "Rear lamp", "vehicle_categories": ["M1"], "functions": ["stop lamp"],
                                "parts": ["lens"], "unknowns": []},
                    "applicable": [{"regulation": "UN R148", "applies": True, "reason": "signalling lamp"},
                                   {"regulation": "UN R48", "applies": False, "reason": "considered, rejected"},
                                   {"regulation": "UN R999", "applies": True, "reason": "not in the index"}]}
        if "regulations" in schema["properties"]:
            return {"regulations": [{"regulation": "UN R148", "focus": "signalling lamps", "skip": ["Annex 8"]}]}
        if "keep" in schema["properties"]:
            return {"keep": ["P0", "P1", "P999"]}
        return {"paragraphs": [{"id": "P0", "requirements": ["First obligation.", "Second obligation."]},
                               {"id": "P999", "requirements": ["Invented paragraph."]}]}


def test_parser_finds_numbered_and_annex_paragraphs():
    labels = {p.label: p for p in corpus.paragraphs("UN R148")}
    assert labels["3.3.1.1"].page == 6
    assert labels["Annex 8 1.2.1"].page == 47
    assert "UN R" not in labels["3.3.1.1"].text  # running headers are stripped


def test_pipeline_cites_only_parsed_paragraphs():
    out = pipeline.run("Rear lamp", [], FakeLLM())
    assert [a["regulation"] for a in out["applicable"]] == ["UN R148"]  # unknown regulation dropped
    assert not any(r.paragraph.label.startswith("Annex 8 ") for r in out["requirements"])  # skipped by scope
    assert out["requirements"] and len(out["requirements"]) % 2 == 0  # invented id P999 dropped everywhere
    assert all(r.text != "Invented paragraph." for r in out["requirements"])
    sub = pipeline.submission("team-x", "dev-rear-lamp", out["requirements"])
    assert checks.validate(sub)["valid"]
    assert checks.score(sub, "dev-rear-lamp")["regulation_level"]["precision"] == 1.0


class DisagreeingLLM(FakeLLM):
    """Writing passes disagree: every call keeps P0, only the first call of each batch position also keeps P1."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def json(self, *, model, system, content, schema, effort="low"):
        if "paragraphs" in schema["properties"]:
            self.calls += 1
            kept = [{"id": "P0", "applies": True, "requirements": ["Agreed obligation."]}]
            if self.calls == 1:
                kept.append({"id": "P1", "requirements": ["Disputed obligation."]})
            return {"paragraphs": [{k: v for k, v in item.items() if k != "applies"} for item in kept]}
        if "decisions" in schema["properties"]:  # the arbiter rejects every disputed paragraph
            return {"decisions": [{"id": "P0", "applies": False}]}
        return super().json(model=model, system=system, content=content, schema=schema, effort=effort)


def test_disputed_paragraphs_go_to_the_arbiter():
    llm = DisagreeingLLM()
    out = pipeline.run("Rear lamp", [], llm)
    texts = [r.text for r in out["requirements"]]
    assert "Agreed obligation." in texts  # kept by every pass
    assert "Disputed obligation." not in texts  # kept by one pass; the arbiter overruled it


def test_api_run_end_to_end(tmp_path, monkeypatch):
    from certivity import api, runs

    monkeypatch.setattr(runs, "LLM", FakeLLM)
    monkeypatch.setattr(runs, "credentials_configured", lambda: True)
    monkeypatch.setattr(api, "store", runs.RunStore(tmp_path))
    with TestClient(api.app) as client:
        run_id = client.post("/api/runs", data={"package_case": "dev-rear-lamp"}).json()["run_id"]
        for _ in range(100):
            status = client.get(f"/api/runs/{run_id}").json()
            if status["finished"]:
                break
            time.sleep(0.1)
        assert status["state"] in {"completed", "partial"}, status["errors"]
        view = client.get(f"/api/runs/{run_id}/results").json()
        req = view["requirements"][0]
        assert req["source_link"]["pdf_page_index"] == req["source"]["page"] - 1
        assert view["passages"][req["primary_passage_id"]]["text"]
        page = client.get(f"/api/documents/{req['source_link']['document_id']}/pages/{req['source_link']['pdf_page_index']}")
        assert page.headers["content-type"] == "image/png"
        assert client.put(f"/api/runs/{run_id}/reviews/{req['draft_id']}", json={"status": "approved"}).status_code == 200
        if settings.team_id:
            assert client.get(f"/api/runs/{run_id}/submission").status_code == 200
