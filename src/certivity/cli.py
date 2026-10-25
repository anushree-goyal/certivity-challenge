"""certivity serve | run | fetch"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from certivity.config import settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="certivity")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="API and UI on http://127.0.0.1:8000")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    run = sub.add_parser("run", help="process one package case and write the submission JSON")
    run.add_argument("--case", required=True)
    run.add_argument("--out", help="default: <case>_<team>.json")
    sub.add_parser("fetch", help="download the base regulation PDFs with the package's own tool")
    goal = sub.add_parser("subgoal", help="sub-goal: which old requirements change in the new regulation version")
    goal.add_argument("--case", required=True, help="folder under subgoal/, e.g. dev or heldout")
    goal.add_argument("--out", help="default: subgoal-<case>_<team>.json")
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn
        uvicorn.run("certivity.api:app", host=args.host, port=args.port)
        return 0

    if args.command == "fetch":
        tool = (settings.package_dir / "tools" / "fetch_regulations.py").resolve()
        status = subprocess.call([sys.executable, str(tool), "--role", "base"], cwd=settings.package_dir)
        # The sub-goal's old/new PDFs, with the tool's own checksum-verified download (it only fetches them
        # when run without filters, which would also download every latest regulation version).
        import certifi

        from certivity.checks import _tool
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())  # python.org builds on macOS ship no CA certificates
        fetch_one = _tool("fetch_regulations").fetch_one
        for listing in sorted(settings.package_dir.glob("subgoal/*/files.json")):
            for item in json.loads(listing.read_text()):
                label = f"sub-goal {listing.parent.name}/{item['name']}"
                print(f"{label}: {fetch_one({'regulation': label, **item}, listing.parent, name=item['name'])}")
        return status

    if args.command == "subgoal":
        from certivity import subgoal

        folder = settings.package_dir / "subgoal" / args.case
        if not (folder / "new.pdf").is_file():
            parser.error(f"no sub-goal case {folder} with old.pdf/new.pdf (run certivity fetch)")
        record = subgoal.execute(args.case, emit=lambda **e: print(f"[{e['stage']}] {e['message']}", file=sys.stderr))
        team = settings.team_id or "team-id-not-set"
        sub_json = subgoal.submission(team, record["case_id"], record)
        path = Path(args.out or f"subgoal-{args.case}_{team}.json")
        path.write_text(json.dumps(sub_json, indent=2))
        validation = record["validation"]
        kinds = {k: sum(c["change"] == k for c in record["changes"]) for k in ("modified", "removed", "added")}
        print(f"{record['regulation']} {record['old_version']} -> {record['new_version']}: {kinds} -> {path}")
        if record["unplaced"]:
            print(f"warning: {len(record['unplaced'])} old requirements cite a paragraph not found in old.pdf")
        print("valid" if validation["valid"] else f"INVALID: {validation['errors'][:5]}")
        if record["evaluation"]:
            print("against expected.json:", record["evaluation"])
        u = record["usage"]
        print(f"model calls: {u['calls']} ({u['cached_calls']} cached), about ${u['cost_usd']:.3f} at list prices")
        return 0 if validation["valid"] else 1

    from certivity import checks, pipeline
    from certivity.llm import LLM

    folder = settings.package_dir / "cases" / args.case
    if not folder.is_dir():
        parser.error(f"no case folder {folder}")
    specs = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".md", ".markdown", ".txt"})
    drawings = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
    llm = LLM()
    out = pipeline.run(specs[0].read_text() if specs else "", drawings, llm,
                       emit=lambda **e: print(f"[{e['stage']}] {e['message']}"
                                              + (f" ({e['completed']}/{e['total']})" if e.get("total") else ""),
                                              file=sys.stderr))
    team = settings.team_id or "team-id-not-set"
    submission = pipeline.submission(team, args.case, out["requirements"])
    path = Path(args.out or f"{args.case}_{team}.json")
    path.write_text(json.dumps(submission, indent=2))
    validation = checks.validate(submission)
    print(f"{len(out['requirements'])} requirements from {', '.join(a['regulation'] for a in out['applicable'])} -> {path}")
    print("valid" if validation["valid"] else f"INVALID: {validation['errors'][:5]}")
    for w in out["warnings"]:
        print(f"warning: {w}")
    if score := checks.score(submission, args.case):
        print({level: round(v["f1"], 3) for level, v in score.items()}, "(local F1, regulation / page)")
    u = llm.usage.as_dict()
    print(f"model calls: {u['calls']} ({u['cached_calls']} cached), tokens {u['input_tokens']} in / {u['output_tokens']} out, "
          f"about ${u['cost_usd']:.3f} at list prices")
    for model, (tin, tout) in u["tokens_by_model"].items():
        print(f"  {model}: {u['by_model'][model]} calls, {tin} in / {tout} out")
    return 0 if validation["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
