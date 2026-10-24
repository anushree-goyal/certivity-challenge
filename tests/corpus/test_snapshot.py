"""Snapshot build behaviour on a tiny temporary package made from two real small PDFs."""

from __future__ import annotations

import json
import shutil

import pytest

from certivity.corpus import CorpusPaths, UnknownId, bootstrap, corpus_status, open_corpus
from certivity.corpus.store import build_snapshot, current_snapshot_id

SMALL = ("UN R62", "UN R42")


@pytest.fixture()
def tiny_package(tmp_path, paths):
    index = json.loads((paths.package_root / "regulations" / "index.json").read_text())
    entries = [e for e in index if e["regulation"] in SMALL]
    for entry in entries:
        entry["versions"] = [v for v in entry["versions"] if v["role"] == "base"]
        for version in entry["versions"]:
            for file in version["files"]:
                src = paths.package_root / "regulations" / "pdf" / entry["regulation"].replace(" ", "_") / "base" / file["name"]
                if not src.is_file():
                    pytest.skip("base PDFs not downloaded")
    root = tmp_path / "package"
    (root / "regulations").mkdir(parents=True)
    (root / "regulations" / "index.json").write_text(json.dumps(entries))
    # only R62 is "downloaded"; R42 stays missing
    entry = entries[0] if entries[0]["regulation"] == "UN R62" else entries[1]
    name = entry["versions"][0]["files"][0]["name"]
    dest = root / "regulations" / "pdf" / "UN_R62" / "base"
    dest.mkdir(parents=True)
    shutil.copy(paths.package_root / "regulations" / "pdf" / "UN_R62" / "base" / name, dest / name)
    return CorpusPaths(root, tmp_path / "cache")


def test_build_reports_missing_files_and_reuses_snapshot(tiny_package):
    first = build_snapshot(tiny_package, workers=1)
    assert not first.reused and len(first.documents_imported) == 1
    assert len(first.not_downloaded) == 1 and first.not_downloaded[0].startswith("UN_R42@")
    assert current_snapshot_id(tiny_package) == first.snapshot_id

    second = build_snapshot(tiny_package, workers=1)
    assert second.reused and second.snapshot_id == first.snapshot_id and second.parsed_from_cache == 1

    reader = open_corpus(paths=tiny_package)
    try:
        assert [d.regulation for d in reader.list_documents()] == ["UN R62"]
        assert any(d["code"] == "not_downloaded" for d in reader.diagnostics())
    finally:
        reader.close()
    status = corpus_status(tiny_package)
    assert status["ready"] and status["documents"] == 1 and len(status["not_downloaded"]) == 1


def test_new_file_creates_new_snapshot_and_keeps_old(tiny_package, paths):
    first = build_snapshot(tiny_package, workers=1)
    old_reader = open_corpus(snapshot_id=first.snapshot_id, paths=tiny_package)
    name = "UN_R42_base.pdf"
    dest = tiny_package.package_root / "regulations" / "pdf" / "UN_R42" / "base"
    dest.mkdir(parents=True)
    shutil.copy(paths.package_root / "regulations" / "pdf" / "UN_R42" / "base" / name, dest / name)
    second = build_snapshot(tiny_package, workers=1)
    assert second.snapshot_id != first.snapshot_id and not second.not_downloaded
    # the old snapshot is untouched and still readable by a run that opened it
    assert len(old_reader.list_documents()) == 1
    old_reader.close()
    assert current_snapshot_id(tiny_package) == second.snapshot_id


def test_checksum_mismatch_is_not_imported(tiny_package):
    pdf = next((tiny_package.package_root / "regulations" / "pdf").rglob("*.pdf"))
    pdf.write_bytes(pdf.read_bytes()[:-10])
    report = build_snapshot(tiny_package, workers=1)
    assert report.checksum_mismatch and not report.documents_imported


def test_status_without_snapshot(tmp_path):
    status = corpus_status(CorpusPaths(tmp_path, tmp_path / "cache"))
    assert status["ready"] is False and "build" in status["reason"]
    assert corpus_status(str(tmp_path / "cache"))["ready"] is False


def test_bootstrap_and_open_by_corpus_dir(tiny_package):
    status = bootstrap(tiny_package.package_root, tiny_package.cache_root)
    assert status["ready"] and status["documents"] == 1
    reader = open_corpus(tiny_package.cache_root)
    try:
        assert reader.snapshot_id == status["snapshot_id"]
        doc = reader.list_documents()[0]
        path = reader.get_document_path(doc.document_id)
        assert path.is_file() and path.name == doc.filename
        with pytest.raises(UnknownId):
            reader.get_document_path("nope")
    finally:
        reader.close()
    by_dir = corpus_status(str(tiny_package.cache_root))  # a plain folder path, as the backend passes it
    assert by_dir["ready"] and by_dir["snapshot_id"] == status["snapshot_id"]
    single = open_corpus(tiny_package.snapshots / status["snapshot_id"])  # a snapshot directory also works
    assert single.snapshot_id == status["snapshot_id"]
    single.close()
