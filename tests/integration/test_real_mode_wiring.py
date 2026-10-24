"""Real-mode composition through entry points, using dataclass stand-ins (tests/integration/fake_modules.py)."""

import dataclasses

from fastapi.testclient import TestClient

from certivity.api.app import create_app
from tests.conftest import wait_finished
from tests.integration import fake_modules


def _real_settings(settings):
    eps = {name: f"tests.integration.fake_modules:{attr}" for name, attr in {
        "open_corpus": "open_corpus", "create_llm": "create_llm", "run_extraction": "run_extraction",
        "revise_extraction": "revise_extraction", "review_requirements": "review_requirements",
        "build_submission": "build_submission", "validate_submission": "validate_submission",
        "make_run_input": "make_run_input"}.items()}
    eps["evaluate_submission"] = "tests.integration.fake_modules:missing"  # optional: absent is fine
    return dataclasses.replace(settings, pipeline_mode="real", entry_points={**settings.entry_points, **eps})


def test_real_mode_passes_module_records_between_stages(settings):
    fake_modules.CALLS.clear()
    with TestClient(create_app(_real_settings(settings))) as client:
        assert client.get("/api/health").json()["modules"]["run_extraction"]["ready"] is True
        run = client.post("/api/runs", data={"package_case": "dev-rear-lamp", "team_id": "team-07"}).json()
        assert run["origin"] == "fresh"
        status = wait_finished(client, run["run_id"])
        assert status["state"] == "partial", status["errors"]
        assert status["corpus_snapshot_id"] == "fake-snapshot-1"
        assert fake_modules.CALLS == ["extract", "review", "revise:1", "review"]
        agent_dir = client.app.state.store.module_dir(run["run_id"], "agent")
        assert fake_modules.SEEN == {"cache_dir": agent_dir / "llm-cache", "artifacts_dir": agent_dir}
        assert status_llm(client, run["run_id"]) == "fake-model"

        view = client.get(f"/api/runs/{run['run_id']}/results").json()
        assert view["view_warnings"] == []  # explicit export_mapping was used, not export order
        assert {r["draft_id"]: r["requirement_id"] for r in view["requirements"] if r["requirement_id"]} == {
            "D-1": "R-001", "D-2": "R-002", "D-4": "R-003", "D-5": "R-004", "D-6": "R-005"}
        assert client.get(f"/api/runs/{run['run_id']}/submission").status_code == 200


def test_cached_reuse_only_for_identical_fresh_runs(settings):
    with TestClient(create_app(_real_settings(settings))) as client:
        first = client.post("/api/runs", data={"package_case": "dev-rear-lamp", "team_id": "team-07"}).json()
        wait_finished(client, first["run_id"])

        reused = client.post("/api/runs", data={"package_case": "dev-rear-lamp", "team_id": "team-07",
                                                "reuse_cached": "true"}).json()
        assert reused["origin"] == "cached" and reused["cached_from"] == first["run_id"]
        view = client.get(f"/api/runs/{reused['run_id']}/results").json()
        assert view["run"]["origin"] == "cached" and len(view["requirements"]) == 6
        assert client.get(f"/api/runs/{reused['run_id']}/submission").json() == \
            client.get(f"/api/runs/{first['run_id']}/submission").json()

        other_team = client.post("/api/runs", data={"package_case": "dev-rear-lamp", "team_id": "team-08",
                                                    "reuse_cached": "true"}).json()
        assert other_team["origin"] == "fresh"
        wait_finished(client, other_team["run_id"])


def status_llm(client, run_id):
    return client.app.state.store.read(run_id)["llm_identity"]


def test_missing_model_credentials_fail_before_any_work(settings):
    fake_modules.CALLS.clear()
    fake_modules.CREDENTIALS[0] = False
    try:
        with TestClient(create_app(_real_settings(settings))) as client:
            health = client.get("/api/health").json()
            assert health["llm"]["ready"] is False and health["real_pipeline_ready"] is False
            assert health["llm"]["description"]["credentials_configured"] is False
            run = client.post("/api/runs", data={"package_case": "dev-rear-lamp", "team_id": "team-07"}).json()
            status = wait_finished(client, run["run_id"])
            assert status["state"] == "failed" and "No model credentials" in status["errors"][0]["message"]
            assert fake_modules.CALLS == []
    finally:
        fake_modules.CREDENTIALS[0] = True
