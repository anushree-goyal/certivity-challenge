"""Corpus command line: ``python -m certivity.corpus <command>`` (run with ``src`` on PYTHONPATH)."""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import sys

from . import default_paths, open_corpus
from ._contracts import ReferenceRequest


def _dump(obj) -> None:
    if dataclasses.is_dataclass(obj):
        obj = dataclasses.asdict(obj)
    elif isinstance(obj, list):
        obj = [dataclasses.asdict(o) if dataclasses.is_dataclass(o) else o for o in obj]
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m certivity.corpus")
    parser.add_argument("--package", help="participant package root (default: data/participant-package)")
    parser.add_argument("--cache", help="corpus cache root (default: data/cache/corpus)")
    parser.add_argument("--snapshot", help="snapshot id (default: CURRENT)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download PDFs with the package's fetch tool")
    f.add_argument("--role", default="base", choices=["base", "latest", "all"])
    b = sub.add_parser("build", help="parse PDFs and publish a snapshot")
    b.add_argument("--role", action="append", choices=["base", "latest"], help="repeatable; default base")
    b.add_argument("--workers", type=int)
    sub.add_parser("status", help="readiness and counts")
    pr = sub.add_parser("prune", help="delete old snapshots (never CURRENT); only when no run is using them")
    pr.add_argument("--keep", type=int, default=2)
    sub.add_parser("documents", help="list base documents")
    s = sub.add_parser("sections", help="list sections of a document")
    s.add_argument("document_id")
    r = sub.add_parser("section", help="read a section bundle")
    r.add_argument("section_id")
    r.add_argument("--no-context", action="store_true")
    p = sub.add_parser("passages", help="get passages by id")
    p.add_argument("passage_ids", nargs="+")
    sc = sub.add_parser("scope", help="scope passages of a document")
    sc.add_argument("document_id")
    rf = sub.add_parser("resolve", help="resolve a reference from a passage")
    rf.add_argument("passage_id")
    rf.add_argument("raw_reference")
    rc = sub.add_parser("references", help="reference candidates detected in passages")
    rc.add_argument("passage_ids", nargs="+")
    pg = sub.add_parser("page", help="render one physical page (zero-based)")
    pg.add_argument("document_id")
    pg.add_argument("pdf_page_index", type=int)
    q = sub.add_parser("search", help="full-text search (navigation only)")
    q.add_argument("query")
    q.add_argument("--document")
    d = sub.add_parser("diagnostics", help="import diagnostics")
    d.add_argument("--document")
    d.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    paths = default_paths(args.package, args.cache)

    if args.cmd == "fetch":
        from .acquire import fetch

        reports = [fetch(paths, role=None if args.role == "all" else args.role)]
        for rep in reports:
            print(rep.output)
            if rep.failed:
                print(f"FAILED: {len(rep.failed)}", file=sys.stderr)
        return max(rep.returncode for rep in reports)
    if args.cmd == "build":
        from .store import build_snapshot

        report = build_snapshot(paths, tuple(args.role or ["base"]), workers=args.workers)
        _dump(report.to_json())
        return 0 if not (report.not_downloaded or report.checksum_mismatch or report.parse_failed) else 1
    if args.cmd == "prune":
        from .store import prune_snapshots

        _dump({"removed": prune_snapshots(paths, args.keep)})
        return 0
    if args.cmd == "status":
        from .reader import corpus_status

        _dump(corpus_status(paths))
        return 0

    corpus = open_corpus(args.snapshot, paths)
    if args.cmd == "documents":
        _dump(corpus.list_documents(None))
    elif args.cmd == "sections":
        for sec in corpus.list_sections(args.document_id):
            print(f"{sec.section_id}\t{len(sec.primary_passage_ids)}\t{sec.paragraph_label or ''}\t{sec.heading[:80]}")
    elif args.cmd == "section":
        _dump(corpus.read_section(args.section_id, include_context=not args.no_context))
    elif args.cmd == "passages":
        _dump(corpus.get_passages(args.passage_ids))
    elif args.cmd == "scope":
        _dump(corpus.read_scope(args.document_id))
    elif args.cmd == "resolve":
        _dump(corpus.resolve_reference(ReferenceRequest(args.passage_id, args.raw_reference)))
    elif args.cmd == "references":
        _dump(corpus.list_reference_candidates(args.passage_ids))
    elif args.cmd == "page":
        _dump(corpus.get_page_asset(args.document_id, args.pdf_page_index))
    elif args.cmd == "search":
        for p in corpus.search(args.query, args.document):
            print(f"{p.passage_id}\tp.{p.printed_start_page}\t{p.text[:100]}")
    elif args.cmd == "diagnostics":
        rows = corpus.diagnostics(args.document)
        if args.summary:
            _dump(collections.Counter(f"{r['severity']}:{r['code']}" for r in rows).most_common())
        else:
            _dump(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
