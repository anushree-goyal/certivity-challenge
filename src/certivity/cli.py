"""Command line: start the app, check readiness, prepare the corpus, or run one case headless.

    certivity serve                       API + built frontend on http://127.0.0.1:8000
    certivity health                      what is ready, without secrets
    certivity bootstrap                   download + import the base corpus (corpus module)
    certivity run --case dev-rear-lamp    one run without the server; prints the submission path
    certivity run --case X --fast          the bounded, faster extraction mode
    certivity export --latest              valid submission JSON from the newest run's checkpoints
    certivity apistats --latest            measured provider usage and a throttling diagnosis
    certivity probe                        controlled latency / rate-limit probe against the API

``run`` resumes by itself: finished sections and model answers are both cached
outside the run folder, so a second ``run`` over the same case skips what the
first one completed. ``export`` never calls the model, so it works when the
provider does not.
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
    if getattr(args, "verify_model", False):
        report["model_call"] = _verify_model_usable()
    print(json.dumps(report, indent=2))
    ok = report["real_pipeline_ready"] or args.allow_fixture
    if getattr(args, "verify_model", False) and not report["model_call"]["usable"]:
        print(f"MODEL NOT USABLE: {report['model_call']['detail']}", file=sys.stderr)
        return 1
    return 0 if ok else 1


def _verify_model_usable() -> dict:
    """Spend one cheap call to find out whether the account can actually be billed.

    A configured API key is not a usable one. An exhausted credit balance arrives
    as a 400 on the first real request, so "credentials_configured: true" reports
    ready for an account that cannot make a single call - which is exactly what
    happened, and it cost a run. The only honest check is to make one.

    Deliberately the smallest possible request on the cheapest model.
    """
    from certivity.contracts import LLMCallError, LLMConfigurationError
    from certivity.llm import ProviderConfig, StructuredLLMAdapter

    adapter = StructuredLLMAdapter(
        ProviderConfig(
            model="claude-haiku-4-5-20251001",
            effort=None,
            max_output_tokens=16,
            timeout_s=30.0,
            max_retries=0,
            use_refusal_fallbacks=False,
            cache_prompt_prefix=False,
        )
    )
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
    }
    try:
        adapter.generate_json(
            "health", "Reply {\"ok\": true}.", {}, schema, prompt_version="health-v1"
        )
    except LLMConfigurationError as exc:
        return {"usable": False, "reason": "account_configuration", "detail": str(exc)[:400]}
    except LLMCallError as exc:
        return {"usable": False, "reason": "call_failed", "detail": str(exc)[:400]}
    except Exception as exc:  # pragma: no cover - unexpected transport problem
        return {"usable": False, "reason": type(exc).__name__, "detail": str(exc)[:400]}
    return {
        "usable": True,
        "model": adapter.config.model,
        "telemetry": adapter.telemetry.snapshot()["http"],
    }


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
    import os

    from fastapi.testclient import TestClient

    from certivity.api.app import create_app

    # Set before create_app(), because load_settings() reads the environment.
    extraction = getattr(args, "extraction", None) or (
        "fast" if getattr(args, "fast", False) else None
    )
    if extraction:
        os.environ["CERTIVITY_EXTRACTION_MODE"] = extraction
    if getattr(args, "fresh", False):
        # Keep checkpoints inside the run folder, so this run neither reads nor
        # adds to the shared ones. The existing checkpoints are left untouched.
        os.environ["CERTIVITY_CHECKPOINT_DIR"] = "off"
        print("--fresh: checkpoints confined to this run; saved ones are not read",
              file=sys.stderr)

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


def _resolve_run(settings, run_id: str | None, latest: bool) -> tuple[str, dict]:
    """Pick the run to act on, and return its id and manifest."""
    from certivity.run_store.store import RunStore

    store = RunStore(settings.runs_dir)
    if run_id:
        if not store.exists(run_id):
            raise SystemExit(f"no run {run_id!r} under {settings.runs_dir}")
        return run_id, store.read(run_id)
    runs = store.list_runs()
    if not runs:
        raise SystemExit(f"no runs under {settings.runs_dir}")
    if not latest:
        raise SystemExit("pass --run <id> or --latest")
    # list_runs is newest-first; take the newest that got far enough to have a
    # case id, which is everything past creation.
    manifest = runs[0]
    return manifest["run_id"], manifest


def _checkpoint_dir_of(settings, run_id: str, manifest: dict):
    """Where this run's checkpoints are, preferring what the run itself recorded."""
    import json as _json
    from pathlib import Path as _Path

    from certivity.agent.checkpoint import checkpoint_dir_for
    from certivity.contracts import RunConfig  # noqa: F401  (import check)
    from certivity.run_store.store import RunStore

    store = RunStore(settings.runs_dir)
    recorded = store.module_dir(run_id, "agent") / "checkpoint_location.json"
    if recorded.is_file():
        try:
            directory = _json.loads(recorded.read_text(encoding="utf-8")).get("directory")
            if directory and _Path(directory).is_dir():
                return _Path(directory)
        except (OSError, ValueError):
            pass
    # Fall back to deriving it, which is what a run that never reached the plan
    # stage leaves us with.
    from certivity.agent.prompts import PROMPT_VERSIONS
    from certivity.contracts import make_run_input

    run_input = make_run_input(
        run_id=run_id,
        team_id=manifest.get("team_id") or "team",
        case_id=manifest.get("case_id") or "case",
        corpus_snapshot_id=manifest.get("corpus_snapshot_id") or "",
        input_hash=manifest.get("input_hash") or "",
        specification_text="placeholder",
    )
    return checkpoint_dir_for(
        run_input, None, store.module_dir(run_id, "agent"), prompt_versions=PROMPT_VERSIONS
    )


def _export(args) -> int:
    """Build a validated submission from saved checkpoints. No model call."""
    from pathlib import Path

    from certivity.api.components import open_corpus_only
    from certivity.api.settings import load_settings, team_id_problem
    from certivity.contracts import make_run_input
    from certivity.recover import export_from_checkpoints

    settings = load_settings()
    run_id, manifest = _resolve_run(settings, args.run, args.latest)
    checkpoint_dir = Path(args.checkpoint_dir) if args.checkpoint_dir else _checkpoint_dir_of(settings, run_id, manifest)
    if checkpoint_dir is None or not Path(checkpoint_dir).is_dir():
        print(f"no checkpoints found for run {run_id} (looked in {checkpoint_dir})", file=sys.stderr)
        return 1

    team_id = args.team or manifest.get("team_id") or settings.default_team_id
    if problem := team_id_problem(team_id):
        print(f"refusing to export: {problem}", file=sys.stderr)
        return 1
    case_id = args.case or manifest.get("case_id")

    corpus = open_corpus_only(settings, "real", manifest.get("corpus_snapshot_id") or None)
    run_input = make_run_input(
        run_id=run_id,
        team_id=team_id,
        case_id=case_id,
        corpus_snapshot_id=manifest.get("corpus_snapshot_id") or getattr(corpus, "snapshot_id", ""),
        input_hash=manifest.get("input_hash") or "",
        specification_text="recovered from checkpoints",
    )
    if getattr(args, "from_draft", None):
        from certivity.agent.checkpoint import CheckpointStore
        from certivity.recover import import_artifact_into_checkpoints

        imported = import_artifact_into_checkpoints(args.from_draft, CheckpointStore(checkpoint_dir))
        print(f"imported {imported} draft(s) from {args.from_draft}", file=sys.stderr)

    out = Path(args.out) if args.out else settings.runs_dir / f"submission-{case_id}-{run_id}.json"
    result = export_from_checkpoints(
        run_input, corpus, checkpoint_dir=checkpoint_dir,
        output_path=out, participant_package=settings.participant_package,
    )
    print(json.dumps(result.summary(), indent=2))
    print(f"wrote {out}", file=sys.stderr)
    if not result.valid:
        errors = (result.validation or {}).get("errors") or []
        for err in errors[:10]:
            print(f"  validation: {err}", file=sys.stderr)
        return 1
    if not result.requirements:
        # An empty submission is valid JSON and worth nothing. Reporting success
        # here invites somebody to upload it believing it is good, so this exits
        # non-zero and says where work that is not empty might be.
        print(
            "WARNING: this export contains no requirements. It is well-formed but "
            "scores nothing; do not upload it in place of a run that found something.",
            file=sys.stderr,
        )
        _report_other_checkpoints(settings, checkpoint_dir)
        return 1
    return 0


def _report_other_checkpoints(settings, chosen) -> None:
    """List checkpoint directories that do hold finished sections.

    `--latest` picks the newest run, which is not always the one that got
    furthest - a quick re-run with --fresh is newer than the long run that
    actually read the corpus.
    """
    from pathlib import Path

    from certivity.agent.checkpoint import DEFAULT_CHECKPOINT_BASE, CheckpointStore
    from certivity.api.settings import REPO_ROOT

    base = Path(DEFAULT_CHECKPOINT_BASE)
    if not base.is_absolute():
        base = REPO_ROOT / base
    found = []
    for candidate in sorted(base.glob("*")) if base.is_dir() else []:
        if not candidate.is_dir() or candidate == Path(chosen):
            continue
        load = CheckpointStore(candidate).load()
        if load.completed_ids:
            found.append((candidate, len(load.completed_ids), len(load.drafts())))
    if not found:
        print("  no other checkpoint directory holds finished sections either.",
              file=sys.stderr)
        return
    print("  checkpoints that do hold work (pass one with --checkpoint-dir):",
          file=sys.stderr)
    for candidate, sections, drafts in found:
        print(f"    {candidate}  ({sections} section(s), {drafts} draft(s))",
              file=sys.stderr)


def _apistats(args) -> int:
    """Print what a run measured about its provider traffic."""
    from certivity.api.settings import load_settings
    from certivity.run_store.store import RunStore

    settings = load_settings()
    run_id, _ = _resolve_run(settings, args.run, args.latest)
    store = RunStore(settings.runs_dir)
    try:
        stats = store.read_artifact(run_id, "api_usage.json")
    except (FileNotFoundError, OSError, ValueError):
        stats = None
    if not stats:
        print(f"run {run_id} recorded no API usage statistics "
              "(it predates them, or made no provider call).", file=sys.stderr)
        return 1
    print(json.dumps(stats, indent=2))
    for line in stats.get("diagnosis") or []:
        print(f"  - {line}", file=sys.stderr)
    return 0


def _probe(args) -> int:
    """Measure real latency and find the concurrency the account actually sustains.

    This is the controlled diagnostic the slowdown needed and never got. It
    sends the same small request at a given concurrency, with the SDK's own
    retries off, so a 429 arrives at the caller instead of being absorbed and
    counted as latency. Nothing is cached, so every call is a real round trip.
    """
    import os
    import time
    from concurrent.futures import ThreadPoolExecutor

    os.environ["CERTIVITY_SDK_RETRIES"] = ""  # explicit retries, so attempts are counted
    from certivity.llm import create_llm
    from certivity.contracts import LLMCallError, LLMConfigurationError

    adapter = create_llm(cache_dir=None, max_calls=args.calls + 5, max_retries=args.retries)
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
        "additionalProperties": False,
    }
    instructions = "Reply with the integer you are given, as JSON."

    def one(index: int):
        started = time.monotonic()
        try:
            adapter.generate_json("probe", instructions, {"n": index}, schema,
                                  prompt_version="probe-v1", max_output_tokens=args.max_tokens)
            return (time.monotonic() - started, None)
        except (LLMCallError, LLMConfigurationError) as exc:
            # A configuration error - no credential, no credit - is the answer
            # to "why is this slow", not an error in the probe. It is reported
            # like any other outcome instead of ending the run with a traceback.
            return (time.monotonic() - started, f"{type(exc).__name__}: {exc}"[:200])

    print(f"probing: {args.calls} call(s) at concurrency {args.concurrency} on "
          f"{adapter.config.model} (effort={adapter.config.effort}, "
          f"max_tokens={args.max_tokens}, stream={adapter.config.stream})", file=sys.stderr)
    wall = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        outcomes = list(pool.map(one, range(args.calls)))
    wall = time.monotonic() - wall

    failures = [m for _, m in outcomes if m]
    report = adapter.usage_report()
    report["probe"] = {
        "requested_calls": args.calls,
        "concurrency": args.concurrency,
        "wall_clock_s": round(wall, 1),
        "throughput_per_minute": round(len(outcomes) / wall * 60, 1) if wall else None,
        "failures": failures[:5],
        "failure_count": len(failures),
    }
    print(json.dumps(report, indent=2))
    for line in report["diagnosis"]:
        print(f"  - {line}", file=sys.stderr)
    return 0 if not failures else 1
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
    health.add_argument("--verify-model", action="store_true", dest="verify_model",
                        help="spend one cheap call to prove the account can be billed "
                             "(a configured key is not a usable one)")
    health.set_defaults(func=_health)

    boot = sub.add_parser("bootstrap", help="build or reuse the base corpus snapshot")
    boot.add_argument("--fetch", action="store_true", help="also download missing PDFs (needs network)")
    boot.set_defaults(func=_bootstrap)

    run = sub.add_parser("run", help="run one participant-package case without the server")
    run.add_argument("--case", required=True, help="folder name under cases/, e.g. dev-rear-lamp")
    run.add_argument("--team", help="team_id (default: CERTIVITY_TEAM_ID)")
    run.add_argument("--mode", choices=["real", "fixture"])
    run.add_argument("--extraction", choices=["full", "fast"],
                     help="extraction mode (default: CERTIVITY_EXTRACTION_MODE, else full)")
    run.add_argument("--fast", action="store_true", help="shorthand for --extraction fast")
    run.add_argument("--fresh", action="store_true",
                     help="ignore saved checkpoints and re-read every section")
    run.set_defaults(func=_run)

    exp = sub.add_parser("export", help="valid submission JSON from saved checkpoints (no model call)")
    group = exp.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", help="run id to export")
    group.add_argument("--latest", action="store_true", help="export the newest run")
    exp.add_argument("--out", help="output path (default: runs/submission-<case>-<run>.json)")
    exp.add_argument("--team", help="team_id override")
    exp.add_argument("--case", help="case_id override")
    exp.add_argument("--checkpoint-dir", help="read checkpoints from here instead")
    exp.add_argument("--from-draft", dest="from_draft",
                     help="also import drafts from an earlier run's pipeline_draft.json")
    exp.set_defaults(func=_export)

    stats = sub.add_parser("apistats", help="measured provider usage for a run, and a diagnosis")
    sgroup = stats.add_mutually_exclusive_group(required=True)
    sgroup.add_argument("--run", help="run id")
    sgroup.add_argument("--latest", action="store_true")
    stats.set_defaults(func=_apistats)

    probe = sub.add_parser("probe", help="controlled latency and rate-limit probe")
    probe.add_argument("--calls", type=int, default=8)
    probe.add_argument("--concurrency", type=int, default=4)
    probe.add_argument("--max-tokens", type=int, default=256, dest="max_tokens")
    probe.add_argument("--retries", type=int, default=0,
                       help="retry attempts after the first (0 surfaces a 429 to the caller)")
    probe.set_defaults(func=_probe)
    held = sub.add_parser("heldout", help="pull the package, fetch PDFs, refresh the corpus and run the held-out case")
    held.add_argument("--case", help="case folder (default: the first heldout-* case)")
    held.add_argument("--max-calls", type=int, help="extraction model-call budget (default: CERTIVITY_MAX_MODEL_CALLS)")
    held.add_argument("--review-calls", type=int, help="review model-call budget (default: CERTIVITY_MAX_REVIEW_CALLS)")
    held.add_argument("--extraction", choices=["full", "fast"],
                      help="extraction mode; 'fast' is the bounded run that finishes inside the window")
    held.add_argument("--fast", action="store_true", help="shorthand for --extraction fast")
    held.set_defaults(func=_heldout)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
