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
        print(f"Corpus bootstrap is not available yet ({exc}).\n"
              f"Until the corpus module provides it, download PDFs with:\n"
              f"  python {settings.participant_package / 'tools' / 'fetch_regulations.py'} --role base",
              file=sys.stderr)
        return 1
    bootstrap(settings.participant_package, settings.corpus_dir)
    return 0


def _run(args) -> int:
    from fastapi.testclient import TestClient

    from certivity.api.app import create_app

    with TestClient(create_app()) as client:
        form = {"package_case": args.case, "team_id": args.team or "", "mode": args.mode or ""}
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
        print(json.dumps({k: status[k] for k in ("run_id", "state", "warnings", "errors")}, indent=2))
        app_dir = client.app.state.store.module_dir(run_id, "app")
        if (app_dir / "submission.json").is_file():
            print(f"validated submission: {app_dir / 'submission.json'}", file=sys.stderr)
        return 0 if status["state"] in ("completed", "partial") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="certivity")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start API and frontend")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_serve)

    health = sub.add_parser("health", help="report component, corpus and model readiness")
    health.add_argument("--allow-fixture", action="store_true", help="exit 0 even if only fixture mode is ready")
    health.set_defaults(func=_health)

    sub.add_parser("bootstrap", help="download and import the base corpus").set_defaults(func=_bootstrap)

    run = sub.add_parser("run", help="run one participant-package case without the server")
    run.add_argument("--case", required=True, help="folder name under cases/, e.g. dev-rear-lamp")
    run.add_argument("--team", help="team_id (default: CERTIVITY_TEAM_ID)")
    run.add_argument("--mode", choices=["real", "fixture"])
    run.set_defaults(func=_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
