"""Command line: start the app, check readiness, prepare the corpus, or run one case headless.

    certivity serve                       API + built frontend on http://127.0.0.1:8000
    certivity health                      what is ready, without secrets
    certivity bootstrap                   download + import the base corpus (corpus module)
    certivity run --case dev-rear-lamp    one run without the server; prints the submission path
"""

from __future__ import annotations

import argparse
import json
import sys


def _serve(args) -> int:
    import uvicorn

    from certivity.api.settings import REPO_ROOT

    if not args.no_bootstrap:
        # Builds the corpus snapshot if needed; reuses it in about a second when nothing changed. No network.
        args.fetch = False
        if _bootstrap(args) != 0:
            print("Corpus is not ready; the server starts anyway and /api/health shows why.", file=sys.stderr)
    from certivity.api.settings import load_settings, team_id_problem

    if problem := team_id_problem(load_settings().default_team_id):
        print(f"Warning: {problem}", file=sys.stderr)
    if not (REPO_ROOT / "web" / "dist").is_dir():
        print("Note: the UI is not built, so only /api is served. Build it with: cd web && npm install && npm run build",
              file=sys.stderr)
    uvicorn.run("certivity.api.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
    return 0


def _health(args) -> int:
    from certivity.api.components import readiness
    from certivity.api.settings import load_settings

    report = readiness(load_settings())
    print(json.dumps(report, indent=2))
    return 0 if (report["real_pipeline_ready"] or args.allow_fixture) else 1


def _bootstrap(args) -> int:
    from certivity.api.components import ComponentError, resolve
    from certivity.api.settings import load_settings

    settings = load_settings()
    if not settings.index_path.is_file():
        print(f"Participant package not found at {settings.participant_package}.\n"
              "Clone https://github.com/Certivity/industrial-ai-hackathon there, or set CERTIVITY_PARTICIPANT_PACKAGE.",
              file=sys.stderr)
        return 1
    entry = settings.entry_points.get("bootstrap_corpus") or "certivity.corpus:bootstrap"
    try:
        bootstrap = resolve(entry)
    except ComponentError as exc:
        pdfs = list((settings.participant_package / "regulations" / "pdf").glob("*/base/*.pdf"))
        hint = (f"{len(pdfs)} base PDFs are already downloaded; the corpus module will import them once merged."
                if pdfs else
                f"Until the corpus module provides it, download PDFs with:\n"
                f"  python {settings.participant_package / 'tools' / 'fetch_regulations.py'} --role base")
        print(f"Corpus bootstrap is not available yet ({exc}).\n{hint}", file=sys.stderr)
        return 1
    from certivity.api.components import call_accepting
    from certivity.api.serialization import to_jsonable

    try:
        status = to_jsonable(call_accepting(bootstrap, settings.participant_package, settings.corpus_dir,
                                            **({"fetch_missing": True} if args.fetch else {})))
    except Exception as exc:
        print(f"Corpus bootstrap failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(status, indent=2))
    if status.get("not_downloaded"):
        print(f"{len(status['not_downloaded'])} file(s) not downloaded; run `certivity bootstrap --fetch` to download them.",
              file=sys.stderr)
    return 0 if status.get("ready") else 1


def _run(args) -> int:
    from fastapi.testclient import TestClient

    from certivity.api.app import create_app

    with TestClient(create_app()) as client:
        form = {"package_case": args.case, "team_id": args.team or "", "mode": args.mode or "",
                "dev": "true" if args.mode == "fixture" else "false"}
        response = client.post("/api/runs", data=form)
        if response.status_code >= 400:
            print(response.json().get("detail"), file=sys.stderr)
            return 1
        run_id = response.json()["run_id"]
        print(f"run {run_id} started", file=sys.stderr)
        import time

        last = None
        while True:
            status = client.get(f"/api/runs/{run_id}").json()
            progress = (status.get("progress") or {}).get("message")
            if progress != last:
                print(f"  [{status['stage']}] {progress}", file=sys.stderr)
                last = progress
            if status["finished"]:
                break
            time.sleep(1)
        print(json.dumps({k: status[k] for k in ("run_id", "state", "case_id", "warnings", "errors")}, indent=2))
        # The export is built now, with the current CERTIVITY_TEAM_ID, and validated by the official validator.
        export = client.get(f"/api/runs/{run_id}/submission")
        if export.status_code == 200:
            submission = export.json()
            target = client.app.state.store.module_dir(run_id, "app") / f"{submission['case_id']}_{submission['team_id']}_{run_id}.json"
            target.write_bytes(export.content)
            print(f"validated export ({len(submission['requirements'])} requirements): {target}", file=sys.stderr)
        else:
            print(f"no export: {export.json().get('reason')}", file=sys.stderr)
        return 0 if status["state"] in ("completed", "partial") else 1


def _heldout(args) -> int:
    """Sunday routine: pull the package, download base PDFs, refresh the corpus, run the held-out case."""
    import os
    import subprocess

    from certivity.api import package
    from certivity.api.settings import load_settings, team_id_problem

    if args.max_calls:
        os.environ["CERTIVITY_MAX_MODEL_CALLS"] = str(args.max_calls)
    if args.review_calls:
        os.environ["CERTIVITY_MAX_REVIEW_CALLS"] = str(args.review_calls)
    settings = load_settings()
    pkg = settings.participant_package
    print(f"1/4 git pull in the participant package", file=sys.stderr)
    if subprocess.run(["git", "-C", str(pkg), "pull", "--ff-only"]).returncode != 0:
        print("git pull failed; fix the package checkout and retry", file=sys.stderr)
        return 1
    print("2/4 downloading base regulation PDFs (checksum-verified)", file=sys.stderr)
    env = dict(os.environ)
    try:
        import certifi
        env.setdefault("SSL_CERT_FILE", certifi.where())  # python.org builds on macOS lack CA certificates
    except ImportError:
        pass
    if subprocess.run([sys.executable, "tools/fetch_regulations.py", "--role", "base"], cwd=pkg, env=env).returncode != 0:
        print("some PDFs failed to download; continuing with what is present", file=sys.stderr)
    print("3/4 refreshing the corpus snapshot", file=sys.stderr)
    args.fetch = False
    if _bootstrap(args) != 0:
        print("corpus is not ready; cannot run", file=sys.stderr)
        return 1
    cases = [c["case_id"] for c in package.list_cases(settings) if c["type"] == "main"]
    case = args.case or next((c for c in cases if c.startswith(package.HELD_OUT_PREFIX)), None)
    if not case or case not in cases:
        print(f"held-out case not found (cases: {', '.join(cases)}); is it released yet?", file=sys.stderr)
        return 1
    if problem := team_id_problem(settings.default_team_id):
        print(f"Warning: {problem}", file=sys.stderr)
    print(f"4/4 running {case}", file=sys.stderr)
    args.case, args.team, args.mode = case, None, "real"
    return _run(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="certivity")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start API and frontend")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--no-bootstrap", action="store_true", help="skip the corpus check/build at startup")
    serve.set_defaults(func=_serve)

    health = sub.add_parser("health", help="report component, corpus and model readiness")
    health.add_argument("--allow-fixture", action="store_true", help="exit 0 even if only fixture mode is ready")
    health.set_defaults(func=_health)

    boot = sub.add_parser("bootstrap", help="build or reuse the base corpus snapshot")
    boot.add_argument("--fetch", action="store_true", help="also download missing PDFs (needs network)")
    boot.set_defaults(func=_bootstrap)

    run = sub.add_parser("run", help="run one participant-package case without the server")
    run.add_argument("--case", required=True, help="folder name under cases/, e.g. dev-rear-lamp")
    run.add_argument("--team", help="team_id (default: CERTIVITY_TEAM_ID)")
    run.add_argument("--mode", choices=["real", "fixture"])
    run.set_defaults(func=_run)

    held = sub.add_parser("heldout", help="pull the package, fetch PDFs, refresh the corpus and run the held-out case")
    held.add_argument("--case", help="case folder (default: the first heldout-* case)")
    held.add_argument("--max-calls", type=int, help="extraction model-call budget (default: CERTIVITY_MAX_MODEL_CALLS)")
    held.add_argument("--review-calls", type=int, help="review model-call budget (default: CERTIVITY_MAX_REVIEW_CALLS)")
    held.set_defaults(func=_heldout)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
