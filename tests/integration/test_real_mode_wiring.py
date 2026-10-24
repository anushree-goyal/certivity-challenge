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
        "build_submission": "build_submission", "build_submission_detailed": "build_submission_detailed",
        "validate_submission": "validate_submission",
        "make_run_input": "make_run_input", "corpus_status": "corpus_status"}.items()}
    eps["evaluate_case"] = "tests.integration.fake_modules:missing"  # optional: absent is fine
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
        assert fake_modules.SEEN == {"cache_dir": agent_dir / "llm-cache", "artifacts_dir": agent_dir, "max_calls": None}
        assert status_llm(client, run["run_id"]) == "fake-model"

        view = client.get(f"/api/runs/{run['run_id']}/results").json()
        assert view["view_warnings"] == []  # explicit export_mapping was used, not export order
        assert {r["draft_id"]: r["requirement_id"] for r in view["requirements"] if r["requirement_id"]} == {
            "D-1": "Q-D-1", "D-2": "Q-D-2", "D-4": "Q-D-4", "D-5": "Q-D-5", "D-6": "Q-D-6"}
        assert view["review_complete"] is True
        assert view["export_excluded"] == [{"draft_id": "D-3", "reason": "duplicate"}]
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


def test_incomplete_review_is_partial_not_verified(settings):
    fake_modules.REVIEW_INCOMPLETE[0] = True
    try:
        with TestClient(create_app(_real_settings(settings))) as client:
            run = client.post("/api/runs", data={"package_case": "dev-rear-lamp", "team_id": "team-07"}).json()
            status = wait_finished(client, run["run_id"])
            assert status["state"] == "partial"
            assert any("review_incomplete" in w for w in status["warnings"])
            assert client.get(f"/api/runs/{run['run_id']}/results").json()["review_complete"] is False
    finally:
        fake_modules.REVIEW_INCOMPLETE[0] = False


def test_sources_stay_viewable_after_reimport_and_corpus_errors_map_to_http(settings):
    with TestClient(create_app(_real_settings(settings))) as client:
        health = client.get("/api/health").json()["corpus"]
        assert health["ready"] is True and health["snapshot_id"] == "fake-snapshot-1" and health["base_documents"] == 2

        run = client.post("/api/runs", data={"package_case": "dev-rear-lamp", "team_id": "team-07"}).json()
        wait_finished(client, run["run_id"])
        link = next(r for r in client.get(f"/api/runs/{run['run_id']}/results").json()["requirements"] if r["source_link"])["source_link"]

        fake_modules.CURRENT_SNAPSHOT[0] = "fake-snapshot-2"  # a new import happened
        try:
            page = client.get(f"/api/documents/{link['document_id']}/pages/{link['pdf_page_index']}",
                              params={"snapshot_id": "fake-snapshot-1"})
            assert page.status_code == 200  # served from the run's pinned snapshot
            assert client.get("/api/documents/UN_R48/pdf").status_code == 404           # UnknownId
            missing = client.get(f"/api/documents/{link['document_id']}/pdf")           # FileNotFoundError
            assert missing.status_code == 500 and "missing or changed" in missing.json()["detail"]
        finally:
            fake_modules.CURRENT_SNAPSHOT[0] = "fake-snapshot-1"


def test_source_pages_need_only_the_corpus_module(settings):
    # Only the corpus entry point is real; agent/quality/llm are missing, as before those branches merge.
    eps = {**settings.entry_points, "open_corpus": "tests.integration.fake_modules:open_corpus"}
    with TestClient(create_app(dataclasses.replace(settings, pipeline_mode="real", entry_points=eps))) as client:
        assert client.get("/api/health").json()["real_pipeline_ready"] is False
        docs = client.get("/api/documents").json()
        assert isinstance(docs, list) and docs
        assert client.get(f"/api/documents/{docs[0]['document_id']}/pages/0").status_code == 200


def test_model_call_budget_reaches_the_adapter(settings):
    s = _real_settings(settings)
    s = dataclasses.replace(s, max_model_calls=7, entry_points={
        **s.entry_points, "run_config_record": "tests.integration.fake_modules:RunConfig"})
    with TestClient(create_app(s)) as client:
        run = client.post("/api/runs", data={"package_case": "dev-rear-lamp", "team_id": "team-07"}).json()
        wait_finished(client, run["run_id"])
        assert fake_modules.SEEN["max_calls"] == 7
        assert client.app.state.store.read(run["run_id"])["model_call_budget"] == 7
