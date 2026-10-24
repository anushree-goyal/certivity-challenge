"""Participant-package handling: regulations library, corpus freshness, re-import, case detection, no leaks."""

import dataclasses
import json
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from certivity.api.app import create_app
from tests.api import stub_corpus
from tests.conftest import wait_finished

STUB = {"open_corpus": "tests.api.stub_corpus:open_corpus", "corpus_status": "tests.api.stub_corpus:corpus_status",
        "bootstrap_corpus": "tests.api.stub_corpus:bootstrap"}


@pytest.fixture
def pkg(tmp_path, package):
    """A writable copy of the participant package without PDFs (the real package is never modified)."""
    copy = tmp_path / "package"
    for name in ("regulations/index.json", "tools", "schemas", "cases"):
        src, dst = package / name, copy / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    return copy


@pytest.fixture
def stub_settings(settings, pkg):
    stub_corpus.reset()
    yield dataclasses.replace(settings, participant_package=pkg, entry_points={**settings.entry_points, **STUB})
    stub_corpus.reset()


def _index_entry(pkg, regulation):
    return next(e for e in json.loads((pkg / "regulations/index.json").read_text()) if e["regulation"] == regulation)


def _download(pkg, package, regulation, slug):
    """Copy one real, checksum-verified base PDF into the package copy, as fetch_regulations.py would."""
    src = next((package / "regulations/pdf" / slug / "base").glob("*.pdf"), None)
    if src is None:
        pytest.skip(f"{regulation} base PDF not downloaded in the real package")
    dst = pkg / "regulations/pdf" / slug / "base" / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return _index_entry(pkg, regulation)["versions"][0]["files"][0]["sha256"]


def test_regulations_lists_every_index_entry_with_flags(stub_settings, pkg, package):
    r48 = _download(pkg, package, "UN R48", "UN_R48")
    bad = pkg / "regulations/pdf/UN_R10/base/UN_R10_base.pdf"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"%PDF-1.4 not the indexed file")
    stub_corpus.DOCUMENTS.append({"document_id": "doc-r48", "regulation": "UN R48", "role": "base", "checksum": r48, "page_count": 122})
    with TestClient(create_app(stub_settings)) as client:
        body = client.get("/api/regulations").json()
    index = json.loads((pkg / "regulations/index.json").read_text())
    rows = {r["regulation"]: r for r in body["regulations"]}
    assert len(rows) == len(index)
    assert rows["UN R48"] | {} == rows["UN R48"]
    assert (rows["UN R48"]["pdf"], rows["UN R48"]["in_corpus"], rows["UN R48"]["page_count"], rows["UN R48"]["document_id"]) == (
        "verified", True, 122, "doc-r48")
    assert rows["UN R48"]["base_version"] == "9.3.0" and rows["UN R48"]["latest_version"] == "9.4.0"
    assert (rows["UN R10"]["pdf"], rows["UN R10"]["in_corpus"]) == ("checksum mismatch", False)
    assert (rows["UN R3"]["pdf"], rows["UN R3"]["downloaded"], rows["UN R3"]["in_corpus"]) == ("missing", False, False)
    assert body["summary"] == {"in_index": len(index), "downloaded": 1, "imported": 1, "corpus_ready": True}
    text = json.dumps(body)
    assert "storage.googleapis.com" not in text and "pdf_url" not in text  # expiring download links never shown
    assert rows["UN R48"]["source_url"] in (None, "https://unece.org/transport/vehicle-regulations")


def test_health_reports_missing_pdfs_and_changed_index(stub_settings, pkg, package):
    r48 = _download(pkg, package, "UN R48", "UN_R48")
    with TestClient(create_app(stub_settings)) as client:
        fresh = client.get("/api/health").json()["corpus_freshness"]
        assert fresh["needs_import"] and fresh["not_imported"] == ["UN R48"]  # downloaded but not in the snapshot
        assert len(fresh["missing_pdfs"]) == fresh["index_entries"] - 1

        stub_corpus.DOCUMENTS.append({"document_id": "doc-r48", "regulation": "UN R48", "role": "base", "checksum": r48, "page_count": 1})
        assert client.get("/api/health").json()["corpus_freshness"]["needs_import"] is False

        index = json.loads((pkg / "regulations/index.json").read_text())  # a pulled package changes R48's file
        next(e for e in index if e["regulation"] == "UN R48")["versions"][0]["files"][0]["sha256"] = "0" * 64
        (pkg / "regulations/index.json").write_text(json.dumps(index))
        changed = client.get("/api/health").json()["corpus_freshness"]
        assert changed["needs_import"] and changed["no_longer_listed"] == ["UN R48"]
        assert any("UN R48" in m for m in changed["checksum_mismatch"])


def test_reimport_builds_new_snapshot_and_is_refused_during_runs(stub_settings):
    with TestClient(create_app(stub_settings, start_worker=False)) as client:
        queued = client.post("/api/runs", data={"team_id": "t", "package_case": "dev-rear-lamp"})
        assert queued.status_code == 202
        refused = client.post("/api/corpus/reimport")
        assert refused.status_code == 409 and "running or queued" in refused.json()["detail"]

    with TestClient(create_app(stub_settings)) as client:
        stub_corpus.BOOTSTRAP_GATE.clear()  # hold the import open
        started = client.post("/api/corpus/reimport")
        assert started.status_code == 202 and started.json()["state"] == "running"
        assert client.post("/api/corpus/reimport").status_code == 409
        blocked = client.post("/api/runs", data={"team_id": "t", "package_case": "dev-rear-lamp"})
        assert blocked.status_code == 409 and "re-imported" in blocked.json()["detail"]
        stub_corpus.BOOTSTRAP_GATE.set()
        for _ in range(100):
            status = client.get("/api/corpus/reimport").json()
            if status["state"] != "running":
                break
            time.sleep(0.05)
        assert status["state"] == "done" and status["snapshot_id"] == "stub-snap-2"
        assert stub_corpus.BOOTSTRAP_CALLS == [(str(stub_settings.participant_package), str(stub_settings.corpus_dir))]
        assert client.get("/api/health").json()["corpus_reimport"]["state"] == "done"


def test_case_detection_spec_only_image_only_subgoal_and_held_out_first(settings, pkg):
    s = dataclasses.replace(settings, participant_package=pkg)
    with TestClient(create_app(s, start_worker=False)) as client:
        before = [c["case_id"] for c in client.get("/api/cases").json()]
        (pkg / "cases/test-spec-only").mkdir()
        (pkg / "cases/test-spec-only/spec.md").write_text("# spec: Bracket\n", encoding="utf-8")
        (pkg / "cases/test-image-only").mkdir()
        (pkg / "cases/test-image-only/Front_View.JPG").write_bytes(b"\xff\xd8\xff fake jpeg")
        (pkg / "cases/test-image-only/side.webp").write_bytes(b"RIFF fake webp")
        (pkg / "cases/heldout-front-bumper").mkdir()
        (pkg / "cases/heldout-front-bumper/notes.txt").write_text("Front bumper\n", encoding="utf-8")
        (pkg / "subgoal/sub-dev").mkdir(parents=True)
        cases = {c["case_id"]: c for c in client.get("/api/cases").json()}
        order = [c["case_id"] for c in client.get("/api/cases").json()]
    assert before == ["dev-rear-lamp"]
    assert order[0] == "heldout-front-bumper" and cases["heldout-front-bumper"]["spec_file"] == "notes.txt"
    assert (cases["test-spec-only"]["spec_file"], cases["test-spec-only"]["drawings"]) == ("spec.md", [])
    assert (cases["test-image-only"]["spec_file"], cases["test-image-only"]["drawings"]) == (None, ["Front_View.JPG", "side.webp"])
    assert cases["sub-dev"]["type"] == "subgoal" and cases["dev-rear-lamp"]["type"] == "main"
    assert all("expected" not in f for c in cases.values() for f in c["spec_files"] + c["drawings"])


def test_spec_only_and_image_only_cases_run_end_to_end_with_folder_case_id(settings, pkg):
    s = dataclasses.replace(settings, participant_package=pkg)
    (pkg / "cases/test-spec-only").mkdir()
    (pkg / "cases/test-spec-only/spec.md").write_text("# spec: Bracket\n", encoding="utf-8")
    (pkg / "cases/test-image-only").mkdir()
    (pkg / "cases/test-image-only/view.png").write_bytes(b"\x89PNG\r\n\x1a\n fake png")
    with TestClient(create_app(s)) as client:
        assert client.post("/api/runs", data={"team_id": "t", "package_case": "sub-dev"}).status_code == 422  # not a main case
        for case in ("test-spec-only", "test-image-only"):
            run = client.post("/api/runs", data={"team_id": "team-07", "package_case": case}).json()
            status = wait_finished(client, run["run_id"])
            assert status["state"] in ("completed", "partial"), status["errors"]
            assert client.get(f"/api/runs/{run['run_id']}/submission").json()["case_id"] == case


def test_no_response_contains_paths_or_expected_answers(settings, pkg, package):
    s = dataclasses.replace(settings, participant_package=pkg)
    expected = json.loads((package / "cases/dev-rear-lamp/expected.json").read_text())
    golden_texts = [r["text"][:60] for r in expected["requirements"][:20]]
    with TestClient(create_app(s)) as client:
        run_id = client.post("/api/runs", data={"team_id": "team-07", "package_case": "dev-rear-lamp"}).json()["run_id"]
        wait_finished(client, run_id)
        bodies = [client.get(u).text for u in (
            "/api/health", "/api/cases", "/api/regulations", "/api/runs", f"/api/runs/{run_id}",
            f"/api/runs/{run_id}/results", f"/api/runs/{run_id}/submission", "/api/documents?mode=fixture")]
    for body in bodies:
        assert str(pkg) not in body and str(settings.runs_dir) not in body and str(settings.corpus_dir) not in body
        assert "/Users/" not in body and "/private/" not in body
        assert "expected.json" not in body
        assert not any(t in body for t in golden_texts)
