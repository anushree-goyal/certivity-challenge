"""Checks against the real supplied base PDFs (skipped when the corpus has not been built)."""

from __future__ import annotations

import sqlite3
import threading

import pymupdf
import pytest

from certivity.corpus import UnknownId
from certivity.corpus._contracts import ReferenceRequest
from certivity.corpus.registry import load_index


def test_registry_metadata_matches_index(corpus, docs, paths):
    specs = {s.document_id: s for s in load_index(paths.package_root) if s.role == "base"}
    assert len(docs) == len(specs) == 56
    r48 = docs["UN R48"]
    spec = specs[r48.document_id]
    assert (r48.version_label, r48.role, r48.filename, r48.checksum) == ("9.3.0", "base", "UN_R48_base.pdf", spec.sha256)
    assert r48.page_count == 122 and r48.snapshot_id == corpus.snapshot_id
    assert not hasattr(r48, "file_path")  # server-only path is not part of the public record


def test_every_page_has_a_verified_printed_label(corpus):
    assert not [d for d in corpus.diagnostics() if d["code"] in ("footer_missing", "footer_mismatch", "footer_identity_mismatch")]


def test_known_paragraph_text_and_page(corpus, docs, by_label):
    p = by_label(docs["UN R48"].document_id, "6.10.4.2")
    assert p.printed_start_page == 50 and p.pdf_page_indices == [49]
    assert "not less than 350 mm nor more than 1,500 mm" in p.text
    assert p.heading_ancestry[-2:] == ["6. Individual specifications", "6.10. Rear position lamp"] or "6.10.4. Position" in p.heading_ancestry[-1]


def test_paragraph_spanning_pages_keeps_both_pages(corpus, docs):
    doc = docs["UN R48"].document_id
    spanning = [p for s in corpus.list_sections(doc) for p in corpus.read_section(s.section_id, False).primary_passages if "spans_pages" in p.parsing_flags and "kind:paragraph" in p.parsing_flags]
    assert spanning, "expected at least one cross-page paragraph in R48"
    p = spanning[0]
    assert len(p.page_span) == 2 and p.page_span[1] == p.page_span[0] + 1
    assert p.pdf_page_indices == [n - 1 for n in p.page_span]


def test_repeated_label_across_annex_parts_stays_distinct(corpus, docs):
    doc = docs["UN R150"].document_id
    hits = [p for s in corpus.list_sections(doc) for p in corpus.read_section(s.section_id, False).primary_passages if p.paragraph_label and p.paragraph_label.endswith(" 1.1") and "kind:paragraph" in p.parsing_flags]
    labels = {p.paragraph_label for p in hits}
    assert {"Annex 6 Part 6 1.1"} <= labels and len(labels) >= 2
    assert len({p.passage_id for p in hits}) == len(hits)
    assert len({p.section_id for p in hits}) == len(hits)


def test_table_keeps_numeric_values(corpus, docs):
    doc = docs["UN R150"].document_id
    tables = [p for s in corpus.list_sections(doc) if s.namespace == "Annex 6 Part 6" for p in corpus.read_section(s.section_id, False).primary_passages if "kind:table" in p.parsing_flags]
    text = "\n".join(t.text for t in tables)
    assert "Table A6-1" in text and "0.51 ± 0.02" in text and "50 ± 10" in text


def test_section_bundle_separates_primary_and_context(corpus, docs):
    doc = docs["UN R48"].document_id
    section = next(s for s in corpus.list_sections(doc) if s.paragraph_label == "6.10")
    bundle = corpus.read_section(section.section_id)
    primary_ids = {p.passage_id for p in bundle.primary_passages}
    assert [p.passage_id for p in bundle.primary_passages] == section.primary_passage_ids
    assert primary_ids.isdisjoint(p.passage_id for p in bundle.context_passages)
    roles = {f for p in bundle.context_passages for f in p.parsing_flags if f.startswith("context:")}
    assert roles == {"context:parent", "context:definition"}
    defined = [p.text for p in bundle.context_passages if "context:definition" in p.parsing_flags]
    assert any('"Rear position lamp" means' in t for t in defined)
    assert corpus.read_section(section.section_id, include_context=False).context_passages == []


def test_every_document_has_scope_passages(corpus, docs):
    for doc in docs.values():
        scope = corpus.read_scope(doc.document_id)
        assert scope and not any("scope_section_not_detected" in p.parsing_flags for p in scope), doc.regulation


def test_reference_resolution_statuses(corpus, docs, by_label):
    r48 = by_label(docs["UN R48"].document_id, "6.10.5.1")
    annex = by_label(docs["UN R150"].document_id, "Annex 6 Part 6 3")

    def resolve(origin, raw):
        return corpus.resolve_reference(ReferenceRequest(origin.passage_id, raw))

    res = resolve(r48, "paragraph 5.8.1. above")
    assert res.status == "resolved" and res.candidate_passage_ids == [r48.passage_id.replace("/6.10.5.1", "/5.8.1")]
    res = resolve(r48, "paragraph 6.2 of UN Regulation No. 10")
    assert res.status == "resolved" and res.candidate_passage_ids[0].startswith(docs["UN R10"].document_id)
    assert resolve(r48, "UN Regulation No. 999").status == "outside_corpus"
    assert resolve(r48, "paragraph 99.1.").status == "missing"
    res = resolve(annex, "paragraph 1.1 of this annex")
    assert res.status == "resolved" and corpus.get_passages(res.candidate_passage_ids)[0].paragraph_label == "Annex 6 Part 6 1.1"
    res = resolve(annex, "paragraph 5.1.3.2.2. of this Regulation")
    assert res.status == "resolved" and corpus.get_passages(res.candidate_passage_ids)[0].paragraph_label == "5.1.3.2.2"
    # plain "paragraph 2." from inside an annex part: label exists in the main body and in the part -> do not guess
    res = resolve(annex, "paragraph 2.")
    assert res.status == "ambiguous" and len(res.candidate_passage_ids) == 2
    labels = {p.paragraph_label for p in corpus.get_passages(res.candidate_passage_ids)}
    assert labels == {"2", "Annex 6 Part 6 2"}


def test_reference_candidates_are_ready_requests(corpus, docs, by_label):
    p = by_label(docs["UN R48"].document_id, "6.10.2")
    requests = corpus.list_reference_candidates([p.passage_id])
    assert {r.target_regulation for r in requests} >= {"UN R7", "UN R148"}
    for r in requests:
        assert corpus.resolve_reference(r).status == "resolved"


def test_page_asset_renders_exact_physical_page(corpus, docs):
    doc = docs["UN R48"].document_id
    asset = corpus.get_page_asset(doc, 49)
    assert (asset.pdf_page_index, asset.printed_page, asset.media_type) == (49, "50", "image/png")
    pixmap = pymupdf.Pixmap(asset.asset_ref)
    assert pixmap.width > 1000 and pixmap.height > pixmap.width
    with pytest.raises(UnknownId):
        corpus.get_page_asset(doc, 122)


def test_unknown_ids_raise(corpus, docs):
    # get_passages omits unknown IDs (contract); the other lookups raise
    known = corpus.list_sections(docs["UN R48"].document_id)[3].primary_passage_ids[0]
    assert corpus.get_passages(["nope"]) == []
    assert [p.passage_id for p in corpus.get_passages(["nope", known, known])] == [known]
    with pytest.raises(UnknownId):
        corpus.read_section("nope")
    with pytest.raises(UnknownId):
        corpus.list_sections("nope")
    assert corpus.resolve_reference(ReferenceRequest("nope", "paragraph 1")).status == "missing"


def test_get_passages_preserves_order(corpus, docs):
    doc = docs["UN R48"].document_id
    ids = corpus.list_sections(doc)[5].primary_passage_ids[:3]
    assert [p.passage_id for p in corpus.get_passages(list(reversed(ids)))] == list(reversed(ids))


def test_snapshot_is_read_only_and_thread_safe(corpus, docs):
    with pytest.raises(sqlite3.OperationalError):
        corpus._db.execute("DELETE FROM passages")
    results = []

    def worker():
        results.append(len(corpus.list_sections(docs["UN R10"].document_id)))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1 and results[0] > 0
