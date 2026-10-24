from urllib.parse import quote
import json

from fastapi.testclient import TestClient

from certivity.api.app import create_app
from certivity.run_store import RunStore
from tests.conftest import wait_finished


def test_health_reports_readiness_without_secrets(settings, monkeypatch):
    monkeypatch.setenv("CERTIVITY_LLM_API_KEY", "sk-should-not-appear")
    monkeypatch.setenv("CERTIVITY_LLM_MODEL", "some-model")
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/health").json()
    assert body["app"]["ok"] is True
    assert body["participant_package"]["ready"] is True
    assert "dev-rear-lamp" in body["participant_package"]["cases"]
    assert body["llm"]["configuration"] == {"CERTIVITY_LLM_MODEL": "some-model"}
    assert "sk-should-not-appear" not in json.dumps(body)


def test_cases_do_not_expose_expected_answers(settings):
    with TestClient(create_app(settings)) as client:
        cases = client.get("/api/cases").json()
    rear = next(c for c in cases if c["case_id"] == "dev-rear-lamp")
    assert rear["has_spec"] and rear["drawings"] == ["exploded_view.png"]
    assert "requirements" not in json.dumps(cases)


def test_input_validation(settings):
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/runs", data={"case_id": "x"}).status_code == 422
        bad = client.post("/api/runs", data={"case_id": "x"}, files={"drawings": ("a.gif", b"GIF89a", "image/gif")})
        assert bad.status_code == 422
        empty = client.post("/api/runs", data={"case_id": "x"}, files={"spec_file": ("spec.md", b"  \n", "text/markdown")})
        assert empty.status_code == 422  # RunInput would reject it later; refuse at upload instead
        unknown = client.post("/api/runs", data={"package_case": "no-such-case"})
        assert unknown.status_code == 422
        no_team = client.post("/api/runs", data={"case_id": "x", "spec_text": "lamp", "team_id": " "})
        assert no_team.status_code == 202  # falls back to configured default team id


def test_real_mode_without_modules_fails_honestly(settings):
    with TestClient(create_app(settings)) as client:
        run = client.post("/api/runs", data={"package_case": "dev-rear-lamp", "mode": "real"}).json()
        status = wait_finished(client, run["run_id"])
        assert status["state"] == "failed"
        assert "Real pipeline is not available" in status["errors"][0]["message"]
        sub = client.get(f"/api/runs/{run['run_id']}/submission")
        assert sub.status_code == 409 and "failed" in sub.json()["reason"]
        assert client.get(f"/api/runs/{run['run_id']}/results").status_code == 409


def test_restart_marks_unfinished_runs_interrupted(settings):
    store = RunStore(settings.runs_dir)
    manifest = store.create({"team_id": "t", "case_id": "c", "mode": "fixture", "origin": "fixture"}, {"spec.md": b"x"})
    store.update(manifest["run_id"], state="running", stage="extraction")
    with TestClient(create_app(settings, start_worker=False)) as client:
        status = client.get(f"/api/runs/{manifest['run_id']}").json()
    assert status["state"] == "failed" and status["interrupted"] is True
    assert "restarted" in status["errors"][-1]["message"]


def test_unknown_run_and_bad_page_index(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/runs/nope").status_code == 404
        assert client.get("/api/runs/..%2Fetc").status_code == 404
        assert client.get("/api/documents/fixture:UN_R48:9.3.0/pages/-1", params={"mode": "fixture"}).status_code == 422
        assert client.get("/api/documents/missing/pages/0", params={"mode": "fixture"}).status_code == 404


def test_cancel_queued_and_running_runs(settings):
    with TestClient(create_app(settings, start_worker=False)) as client:
        queued = client.post("/api/runs", data={"case_id": "x", "spec_text": "lamp"}).json()
        assert client.post(f"/api/runs/{queued['run_id']}/cancel").json()["cancelled"] is True
        status = client.get(f"/api/runs/{queued['run_id']}").json()
        assert status["state"] == "failed" and "Cancelled" in status["errors"][-1]["message"]
        assert client.post(f"/api/runs/{queued['run_id']}/cancel").status_code == 409

    with TestClient(create_app(settings)) as client:
        run = client.post("/api/runs", data={"package_case": "dev-rear-lamp"}).json()
        client.post(f"/api/runs/{run['run_id']}/cancel")
        status = wait_finished(client, run["run_id"])
        assert status["state"] == "failed"
        assert any("Cancelled" in e["message"] for e in status["errors"])
        assert client.get(f"/api/runs/{run['run_id']}/submission").status_code == 409


def test_compare_two_runs(settings):
    with TestClient(create_app(settings)) as client:
        ids = [client.post("/api/runs", data={"package_case": "dev-rear-lamp"}).json()["run_id"] for _ in range(2)]
        for run_id in ids:
            wait_finished(client, run_id)
        diff = client.get("/api/compare", params={"a": ids[0], "b": ids[1]}).json()
        assert diff["regulations"] == {"both": 2, "only_a": [], "only_b": []}
        assert diff["a"]["requirements"] == diff["b"]["requirements"] == 5
        assert client.get("/api/compare", params={"a": ids[0], "b": "missing"}).status_code == 404


def test_opaque_ids_with_reserved_characters_route_correctly(settings, monkeypatch):
    from certivity.api import fixture_mode

    odd = "un-r48:9-3-0:sha256-ab/cd#e f"
    original = fixture_mode.FixtureCorpus.get_page_asset

    def get_page_asset(self, document_id, pdf_page_index):
        assert document_id == odd
        return {"document_id": odd, "pdf_page_index": pdf_page_index, "printed_page": "3",
                "media_type": "image/svg+xml", "content": b"<svg xmlns='http://www.w3.org/2000/svg'/>"}

    monkeypatch.setattr(fixture_mode.FixtureCorpus, "get_page_asset", get_page_asset)
    with TestClient(create_app(settings)) as client:
        encoded = quote(odd, safe="")
        page = client.get(f"/api/documents/{encoded}/pages/2", params={"mode": "fixture"})
        assert page.status_code == 200 and page.headers["x-printed-page-label"] == "3"
        assert client.get(f"/api/documents/{encoded}/pdf", params={"mode": "fixture"}).status_code == 404
    monkeypatch.setattr(fixture_mode.FixtureCorpus, "get_page_asset", original)
