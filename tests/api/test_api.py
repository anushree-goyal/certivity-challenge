import dataclasses
import json
from urllib.parse import quote

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
    # Point the pipeline at modules that do not exist, independent of which branches are merged.
    missing = {name: f"certivity.not_merged_{name}:{name}" for name in
               ("run_extraction", "review_requirements", "build_submission", "validate_submission", "create_llm")}
    settings = dataclasses.replace(settings, entry_points={**settings.entry_points, **missing})
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


def test_case_id_rule_allows_package_folders(settings):
    with TestClient(create_app(settings, start_worker=False)) as client:
        bad = client.post("/api/runs", data={"team_id": "t", "case_id": "Front Bumper", "spec_text": "lamp"})
        assert bad.status_code == 422 and "lowercase" in bad.json()["detail"]
        assert client.post("/api/runs", data={"team_id": "t", "case_id": "front-bumper-2", "spec_text": "lamp"}).status_code == 202
        assert client.post("/api/runs", data={"team_id": "t", "package_case": "dev-rear-lamp"}).status_code == 202


def test_health_exposes_no_local_paths(settings):
    with TestClient(create_app(settings)) as client:
        text = client.get("/api/health").text
    assert str(settings.participant_package) not in text and str(settings.corpus_dir) not in text
    assert '"path"' not in text


def _package_copy(tmp_path, package):
    """A writable copy of the participant package (index, tools, schemas, cases)."""
    import shutil

    copy = tmp_path / "package"
    for name in ("regulations/index.json", "tools", "schemas", "cases"):
        src, dst = package / name, copy / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    return copy


def test_case_list_reflects_folders_on_disk_without_restart(settings, package, tmp_path):
    settings = dataclasses.replace(settings, participant_package=_package_copy(tmp_path, package))
    with TestClient(create_app(settings, start_worker=False)) as client:
        assert [c["case_id"] for c in client.get("/api/cases").json()] == ["dev-rear-lamp"]
        held_out = settings.cases_dir / "heldout-front-bumper"  # e.g. unzipped on Sunday morning
        held_out.mkdir()
        (held_out / "spec.md").write_text("# spec: Front bumper\n", encoding="utf-8")
        assert "heldout-front-bumper" in [c["case_id"] for c in client.get("/api/cases").json()]
        run = client.post("/api/runs", data={"team_id": "t", "package_case": "heldout-front-bumper"})
        assert run.status_code == 202
        assert client.get(f"/api/runs/{run.json()['run_id']}").json()["case_id"] == "heldout-front-bumper"


def test_package_case_fixes_case_id_to_folder_name(settings):
    with TestClient(create_app(settings, start_worker=False)) as client:
        filled = client.post("/api/runs", data={"team_id": "t", "package_case": "dev-rear-lamp", "case_id": ""})
        assert client.get(f"/api/runs/{filled.json()['run_id']}").json()["case_id"] == "dev-rear-lamp"
        other = client.post("/api/runs", data={"team_id": "t", "package_case": "dev-rear-lamp", "case_id": "something-else"})
        assert other.status_code == 422 and "folder name" in other.json()["detail"]


def test_case_id_is_derived_for_own_input_and_still_validated_when_given(settings):
    with TestClient(create_app(settings, start_worker=False)) as client:
        def case_of(**kw):
            r = client.post("/api/runs", **kw)
            assert r.status_code == 202, r.text
            return client.get(f"/api/runs/{r.json()['run_id']}").json()["case_id"]

        assert case_of(files={"spec_file": ("Front Bumper Spec.md", b"# x\n", "text/markdown")}) == "front-bumper-spec"
        assert case_of(data={"spec_text": "# spec: Front Bumper\n\nbody"}) == "front-bumper"
        assert case_of(data={"spec_text": "Rear lamp module for an M1 passenger car, LED stop lamp\nmore"}) == "rear-lamp-module-for-an-m1"  # first six words
        assert len(case_of(data={"spec_text": "# " + "very long heading words " * 6})) <= 40
        custom = case_of(data={"spec_text": "!!! ???"})
        assert custom.startswith("custom-") and len(custom) == len("custom-") + 6
        assert case_of(files={"drawings": ("Side View.PNG", b"\x89PNG fake", "image/png")}) == "side-view"
        assert case_of(data={"package_case": "dev-rear-lamp"}) == "dev-rear-lamp"
        # API clients that do send a case_id still get the format check.
        assert client.post("/api/runs", data={"case_id": "heldout_front", "spec_text": "x"}).status_code == 422


def test_team_id_comes_from_configuration_and_missing_blocks_export(settings):
    settings = dataclasses.replace(settings, default_team_id="")
    with TestClient(create_app(settings)) as client:
        warning = client.get("/api/health").json()["team_id"]
        assert warning["configured"] is False and "CERTIVITY_TEAM_ID is not set" in warning["warning"]
        run = client.post("/api/runs", data={"package_case": "dev-rear-lamp"})
        assert run.status_code == 202  # the analysis itself still runs
        status = wait_finished(client, run.json()["run_id"])
        assert any("CERTIVITY_TEAM_ID" in w for w in status["warnings"])
        blocked = client.get(f"/api/runs/{run.json()['run_id']}/submission")
        assert blocked.status_code == 409 and blocked.json()["code"] == "team_missing"
        assert blocked.json()["reason"].startswith("Export disabled: team ID not configured")

    # After setting CERTIVITY_TEAM_ID and restarting, the same run exports without re-running.
    restarted = dataclasses.replace(settings, default_team_id="team-real-7")
    with TestClient(create_app(restarted)) as client:
        run_id = run.json()["run_id"]
        assert client.get(f"/api/runs/{run_id}/export").json()["available"] is True
        sub = client.get(f"/api/runs/{run_id}/submission")
        assert sub.status_code == 200 and sub.json()["team_id"] == "team-real-7"
        assert f'filename="dev-rear-lamp_team-real-7_{run_id}.json"' in sub.headers["content-disposition"]


def test_placeholder_team_id_warns_but_exports(settings):
    settings = dataclasses.replace(settings, default_team_id="team-demo")
    with TestClient(create_app(settings)) as client:
        assert "placeholder" in client.get("/api/health").json()["team_id"]["warning"]
        run_id = client.post("/api/runs", data={"package_case": "dev-rear-lamp"}).json()["run_id"]
        wait_finished(client, run_id)
        assert client.get(f"/api/runs/{run_id}/submission").status_code == 200


def test_export_carries_configured_team_and_derived_case(settings):
    settings = dataclasses.replace(settings, default_team_id="team-real-42")
    with TestClient(create_app(settings)) as client:
        run = client.post("/api/runs", files={"spec_file": ("Front Bumper Spec.md", b"# spec: Front bumper\nM1 vehicle\n", "text/markdown")})
        run_id = run.json()["run_id"]
        wait_finished(client, run_id)
        sub = client.get(f"/api/runs/{run_id}/submission")
        assert sub.status_code == 200
        assert (sub.json()["team_id"], sub.json()["case_id"]) == ("team-real-42", "front-bumper-spec")
        assert f'filename="front-bumper-spec_team-real-42_{run_id}.json"' in sub.headers["content-disposition"]
        assert client.get("/api/health").json()["team_id"] == {"configured": True, "warning": None}


def test_fixture_processing_needs_dev_flag(settings):
    settings = dataclasses.replace(settings, pipeline_mode="real")
    with TestClient(create_app(settings, start_worker=False)) as client:
        refused = client.post("/api/runs", data={"team_id": "t", "package_case": "dev-rear-lamp", "mode": "fixture"})
        assert refused.status_code == 422 and "dev=1" in refused.json()["detail"]
        allowed = client.post("/api/runs", data={"team_id": "t", "package_case": "dev-rear-lamp", "mode": "fixture", "dev": "true"})
        assert allowed.status_code == 202 and allowed.json()["origin"] == "fixture"
