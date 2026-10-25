"""certivity serve | run | fetch"""

from __future__ import annotations

import argparse
import json
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
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn
        uvicorn.run("certivity.api:app", host=args.host, port=args.port)
        return 0

    if args.command == "fetch":
        tool = (settings.package_dir / "tools" / "fetch_regulations.py").resolve()
        return subprocess.call([sys.executable, str(tool), "--role", "base"], cwd=settings.package_dir)

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
