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
