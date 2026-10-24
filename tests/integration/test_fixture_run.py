"""End-to-end through the HTTP API with the labelled fixture components and the official validator."""

import importlib.util

from fastapi.testclient import TestClient

from certivity.api.app import create_app
from tests.conftest import wait_finished


def _official_validate(package, data):
    spec = importlib.util.spec_from_file_location("official_validate", package / "tools" / "validate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate(data, index=module.load_index(package / "regulations" / "index.json"))


def test_fixture_run_end_to_end(settings, package):
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/runs", data={"package_case": "dev-rear-lamp", "team_id": "team-07"})
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        assert created.json()["origin"] == "fixture"

        status = wait_finished(client, run_id)
        assert status["state"] == "partial", status  # one coverage item is deliberately pending
        assert status["revision_rounds"] == 1
        assert any("Coverage is partial" in w for w in status["warnings"])
        stages = [e["stage"] for e in status["events"]]
        assert stages.index("extraction") < stages.index("review") < stages.index("revision") < stages.index("export")

        view = client.get(f"/api/runs/{run_id}/results").json()
        assert view["run"]["origin"] == "fixture"
        by_draft = {r["draft_id"]: r for r in view["requirements"]}
        assert by_draft["D-3"]["status"] == "duplicate" and by_draft["D-3"]["duplicate_of"] == "D-2"
        assert by_draft["D-6"]["status"] == "accepted" and "red" in by_draft["D-6"]["text"]
        assert view["coverage"]["counts"] == {"processed": 4, "pending": 1}
        assert view["validation"]["valid"] is True

        # The download is exactly what passed the official validator.
        sub = client.get(f"/api/runs/{run_id}/submission")
        assert sub.status_code == 200
        submission = sub.json()
        assert submission["team_id"] == "team-07" and submission["case_id"] == "dev-rear-lamp"
        assert len(submission["requirements"]) == 5
        assert _official_validate(package, submission) == []

        # Source link: printed page in the citation, physical page served for the viewer.
        link = by_draft["D-1"]["source_link"]
        assert link["printed_page"] == by_draft["D-1"]["source"]["page"]
        page = client.get(f"/api/documents/{link['document_id']}/pages/{link['pdf_page_index']}",
                          params={"mode": "fixture", "snapshot_id": view["run"]["corpus_snapshot_id"]})
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("image/svg+xml")
        assert page.headers["x-printed-page-label"] == str(link["printed_page"])
        assert b"FIXTURE PAGE" in page.content

        wrong_snapshot = client.get(f"/api/documents/{link['document_id']}/pages/0",
                                    params={"mode": "fixture", "snapshot_id": "other"})
        assert wrong_snapshot.status_code == 409

        # Immutable input copies are kept with the run.
        assert client.get(f"/api/runs/{run_id}/input/spec.md").text.startswith("# spec: Rear Lamp")
        assert client.get(f"/api/runs/{run_id}/input/drawing-1.png").status_code == 200

        metrics = client.put(f"/api/runs/{run_id}/platform-metrics", json={"requirement_f1": None, "note": "not uploaded"})
        assert metrics.json()["source"].startswith("entered manually")
