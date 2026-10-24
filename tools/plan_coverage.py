#!/usr/bin/env python3
"""How much of the answer key a section cap can still reach. No model call.

Fast mode reads only the highest-priority ``max_sections_total`` sections, which
raises the obvious question: does the cap throw away the pages the answer key
needs? That is answerable offline, because the plan's order and the corpus's
page labels are both deterministic. Extraction still has to find the right
obligation inside a section, so what this reports is a *ceiling* on page recall,
not a prediction of it - but a ceiling is what decides whether a cap is safe.

    PYTHONPATH=src python tools/plan_coverage.py --run runs/<id>

The run supplies the product profile and the applicability decisions, so this
measures the plan the pipeline would actually build rather than a guess at it.
Without ``--expected`` it reports plan size and what each cap would drop, which
is the only thing available for a held-out case.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_CAPS = (60, 120, 240, 400, 600)


def _load_plan(run_dir: Path, corpus_dir: str):
    from certivity.agent.context import RunContext
    from certivity.agent.plan import build_inspection_plan
    from certivity.contracts import (
        ProductProfile,
        RunConfig,
        RunInput,
        content_hash,
        from_jsonable,
    )
    from certivity.corpus import open_corpus

    corpus = open_corpus(corpus_dir)
    profile = from_jsonable(
        ProductProfile, json.loads((run_dir / "agent" / "product_profile.json").read_text())
    )
    decisions = json.loads((run_dir / "agent" / "applicability.json").read_text())
    wanted = [
        document_id
        for decision in decisions
        if decision.get("status") in ("applicable", "uncertain")
        for document_id in decision["document_ids"]
    ]
    documents = {d.document_id: d for d in corpus.list_documents(role="base")}
    selected = [documents[i] for i in wanted if i in documents]

    run_input = RunInput(
        run_id="plan-coverage",
        team_id="probe",
        case_id=json.loads((run_dir / "manifest.json").read_text()).get("case_id", "case"),
        corpus_snapshot_id=corpus.snapshot_id,
        input_hash=content_hash("plan-coverage"),
        specification_text="plan coverage probe",
        config=RunConfig(max_sections_total=None),
    )
    ctx = RunContext(
        run_input=run_input, corpus=corpus, llm=None, emit_event=lambda *a, **k: None
    )
    plan, _ = build_inspection_plan(ctx, selected, profile)
    return corpus, selected, plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, help="a run directory with agent/ artifacts")
    parser.add_argument("--corpus", default="data/cache/corpus")
    parser.add_argument("--expected", help="an expected.json to measure the ceiling against")
    parser.add_argument("--caps", default=",".join(str(c) for c in DEFAULT_CAPS))
    args = parser.parse_args(argv)

    run_dir = Path(args.run)
    corpus, selected, plan = _load_plan(run_dir, args.corpus)
    print(f"documents selected : {len(selected)} ({', '.join(sorted(d.regulation for d in selected))})")
    print(f"sections planned   : {len(plan)}")

    caps = sorted({int(c) for c in args.caps.split(",") if c.strip()} | {len(plan)})

    if not args.expected:
        print("\nno --expected given, so only the cost of each cap is shown:")
        for cap in caps:
            dropped = max(0, len(plan) - cap)
            print(f"  cap {cap:>5}: reads {min(cap, len(plan)):>5}, leaves {dropped:>5} unread")
        return 0

    expected = json.loads(Path(args.expected).read_text())
    gold_pages = {
        (r["source"]["regulation"], r["source"].get("version"), r["source"]["page"])
        for r in expected["requirements"]
    }
    gold_paragraphs = {
        (r["source"]["regulation"], r["source"].get("version"),
         str(r["source"]["paragraph"]).rstrip("."))
        for r in expected["requirements"]
    }
    print(f"\nanswer key         : {len(gold_pages)} distinct (regulation, version, page), "
          f"{len(gold_paragraphs)} paragraphs")

    identity = {d.document_id: (d.regulation, d.version_label) for d in selected}
    pages: set = set()
    paragraphs: set = set()
    print(f"\n{'cap':>6}  {'gold pages reachable':>24}  {'gold paragraphs reachable':>26}")
    for index, item in enumerate(plan, 1):
        key = identity.get(item.document.document_id)
        if key:
            try:
                passages = corpus.get_passages(list(item.section.primary_passage_ids))
            except Exception:
                passages = []
            for passage in passages:
                page = passage.printed_start_page
                if page and str(page).isdigit():
                    pages.add((key[0], key[1], int(page)))
                if passage.paragraph_label:
                    paragraphs.add((key[0], key[1], str(passage.paragraph_label).rstrip(".")))
        if index in caps:
            hit_pages = len(pages & gold_pages)
            hit_paragraphs = len(paragraphs & gold_paragraphs)
            print(f"{index:>6}  {hit_pages:>8} / {len(gold_pages)} ({hit_pages / len(gold_pages):>6.1%})"
                  f"  {hit_paragraphs:>10} / {len(gold_paragraphs)} "
                  f"({hit_paragraphs / len(gold_paragraphs):>6.1%})")
    print("\nThis is a ceiling, not a score: extraction still has to state the obligation "
          "a page carries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
