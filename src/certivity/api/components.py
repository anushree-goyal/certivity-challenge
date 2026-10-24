"""Bind the four components' public entry points for one run.

"real" mode imports the owners' modules through the configured entry points (see settings.py).
"fixture" mode uses the clearly labelled stand-ins in fixture_mode.py so the API and UI can be
exercised before the real modules exist. Fixture output is never presented as real processing.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from certivity.api.serialization import to_jsonable
from certivity.api.settings import Settings


class ComponentError(RuntimeError):
    """A component entry point is missing or failed to load."""


def resolve(entry_point: str) -> Any:
    module_name, _, attr = entry_point.partition(":")
    if not attr:
        raise ComponentError(f"entry point {entry_point!r} must look like 'package.module:attribute'")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ComponentError(f"cannot import {module_name} ({exc})") from exc
    try:
        return getattr(module, attr)
    except AttributeError:
        raise ComponentError(f"{module_name} has no attribute {attr!r}") from None


@dataclass
class Components:
    mode: str
    open_corpus: Callable[..., Any]  # open_corpus(snapshot_id=None): None opens the current snapshot
    create_llm: Callable[[Path], Any]  # takes the run's agent/ folder for the model-call cache
    run_extraction: Callable[..., Any]
    revise_extraction: Callable[..., Any] | None
    review_requirements: Callable[..., Any]
    build_submission: Callable[..., Any]
    build_submission_detailed: Callable[..., Any] | None
    validate_submission: Callable[..., Any]
    evaluate_submission: Callable[..., Any] | None
    make_run_input: Callable[[dict[str, Any]], Any]


REQUIRED = ("open_corpus", "create_llm", "run_extraction", "review_requirements",
            "build_submission", "validate_submission", "make_run_input")
OPTIONAL = ("revise_extraction", "evaluate_submission", "build_submission_detailed")


def _record_factory(factory: Any) -> Callable[[dict[str, Any]], Any]:
    """Wraps contracts.make_run_input (keyword-only)."""
    def make(fields: dict[str, Any]) -> Any:
        try:
            return factory(**fields)
        except TypeError as exc:
            raise ComponentError(
                f"make_run_input does not accept the app's fields {sorted(fields)}: {exc}. "
                "Align certivity.contracts and api/pipeline.py at the integration checkpoint."
            ) from exc
        except Exception as exc:  # ValueError from RunInput.__post_init__ and similar
            raise ComponentError(f"RunInput rejected the app's input: {exc}") from exc
    return make


def call_accepting(func: Callable[..., Any], *args: Any, **optional: Any) -> Any:
    """Call func, passing only the optional keyword arguments its signature accepts."""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return func(*args)
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return func(*args, **optional)
    return func(*args, **{k: v for k, v in optional.items() if k in params})


def describe_llm(llm: Any) -> dict[str, Any]:
    """The adapter's secret-free self-description (llm.describe()), or what can be inferred without it."""
    describe = getattr(llm, "describe", None)
    if callable(describe):
        return to_jsonable(describe())
    identity = getattr(llm, "identity", None) or getattr(llm, "model", None)
    return {"model": str(identity) if identity else None}


def llm_configured(description: dict[str, Any]) -> bool:
    return description.get("credentials_configured", True) is not False


def open_corpus_only(settings: Settings, mode: str | None = None, snapshot_id: str | None = None) -> Any:
    """Open the corpus without loading the rest of the pipeline: source viewing needs only the corpus module."""
    mode = mode or settings.pipeline_mode
    if mode == "fixture":
        from certivity.api import fixture_mode
        return fixture_mode.FixtureCorpus()
    if mode != "real":
        raise ComponentError(f"unknown pipeline mode {mode!r}")
    open_corpus = resolve(settings.entry_points["open_corpus"])
    if snapshot_id:
        return call_accepting(open_corpus, settings.corpus_dir, snapshot_id=snapshot_id)
    return open_corpus(settings.corpus_dir)


def load_components(settings: Settings, mode: str | None = None) -> Components:
    mode = mode or settings.pipeline_mode
    if mode == "fixture":
        from certivity.api import fixture_mode
        return fixture_mode.components(settings)
    if mode != "real":
        raise ComponentError(f"unknown pipeline mode {mode!r}")

    loaded: dict[str, Any] = {}
    problems = []
    for name in REQUIRED + OPTIONAL:
        try:
            loaded[name] = resolve(settings.entry_points[name])
        except ComponentError as exc:
            if name in REQUIRED:
                problems.append(f"{name}: {exc}")
            loaded[name] = None
    if problems:
        raise ComponentError("Real pipeline is not available. " + "; ".join(problems))

    open_corpus, create_llm = loaded["open_corpus"], loaded["create_llm"]
    return Components(
        mode="real",
        open_corpus=lambda snapshot_id=None: (call_accepting(open_corpus, settings.corpus_dir, snapshot_id=snapshot_id)
                                              if snapshot_id else open_corpus(settings.corpus_dir)),
        create_llm=lambda agent_dir: call_accepting(create_llm, cache_dir=Path(agent_dir) / "llm-cache"),
        run_extraction=loaded["run_extraction"],
        revise_extraction=loaded["revise_extraction"],
        review_requirements=loaded["review_requirements"],
        build_submission=loaded["build_submission"],
        build_submission_detailed=loaded["build_submission_detailed"],
        validate_submission=loaded["validate_submission"],
        evaluate_submission=loaded["evaluate_submission"],
        make_run_input=_record_factory(loaded["make_run_input"]),
    )


# --- readiness and identity -------------------------------------------------------------------


def snapshot_id_of(corpus: Any) -> str | None:
    for name in ("snapshot_id", "corpus_snapshot_id"):
        value = getattr(corpus, name, None)
        if callable(value):
            value = value()
        if value:
            return str(value)
    return None


def component_versions(settings: Settings, mode: str) -> dict[str, str]:
    """Version/prompt-version strings the modules expose, used in the cache key."""
    if mode == "fixture":
        from certivity.api import fixture_mode
        return {"fixture": fixture_mode.FIXTURE_VERSION}
    versions = {}
    for package in ("certivity.contracts", "certivity.agent", "certivity.llm", "certivity.corpus", "certivity.quality"):
        try:
            module = importlib.import_module(package)
        except ImportError:
            continue
        for attr in ("PROMPT_VERSION", "SCHEMA_VERSION", "__version__"):
            if hasattr(module, attr):
                versions[f"{package}.{attr}"] = str(getattr(module, attr))
    return versions


_SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD")


def model_configuration() -> dict[str, str]:
    """Non-secret model settings (CERTIVITY_LLM_*), used in the cache key and shown in health."""
    return {k: v for k, v in sorted(os.environ.items())
            if k.startswith("CERTIVITY_LLM_") and not any(m in k for m in _SECRET_MARKERS)}


def _short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def readiness(settings: Settings) -> dict[str, Any]:
    """What is ready, without making model calls and without exposing secrets."""
    report: dict[str, Any] = {"pipeline_mode": settings.pipeline_mode}

    package = settings.participant_package
    cases = sorted(p.name for p in settings.cases_dir.iterdir() if p.is_dir()) if settings.cases_dir.is_dir() else []
    report["participant_package"] = {
        "path": str(package),
        "ready": settings.index_path.is_file(),
        "cases": cases,
        "validator_present": (package / "tools" / "validate.py").is_file(),
    }

    modules = {}
    for name in REQUIRED + OPTIONAL:
        try:
            resolve(settings.entry_points[name])
            modules[name] = {"ready": True, "entry_point": settings.entry_points[name]}
        except ComponentError as exc:
            modules[name] = {"ready": False, "entry_point": settings.entry_points[name],
                             "required": name in REQUIRED, "error": str(exc)}
    report["modules"] = modules

    corpus_report: dict[str, Any] = {"ready": False, "path": str(settings.corpus_dir)}
    try:
        corpus_status = resolve(settings.entry_points["corpus_status"])
    except ComponentError:
        corpus_status = None
    if corpus_status is not None:
        # The corpus module's own status: never raises, no network.
        status = to_jsonable(corpus_status(settings.corpus_dir)) or {}
        corpus_report.update(status, ready=bool(status.get("ready")), base_documents=status.get("documents"))
    elif modules["open_corpus"]["ready"]:
        try:
            corpus = resolve(settings.entry_points["open_corpus"])(settings.corpus_dir)
            docs = corpus.list_documents(role="base")
            corpus_report.update(ready=True, snapshot_id=snapshot_id_of(corpus), base_documents=len(docs))
        except Exception as exc:
            corpus_report["error"] = _short(exc)
    report["corpus"] = corpus_report

    llm_report: dict[str, Any] = {"ready": False, "configuration": model_configuration()}
    if modules["create_llm"]["ready"]:
        try:
            description = describe_llm(resolve(settings.entry_points["create_llm"])())
            llm_report.update(ready=llm_configured(description), identity=description.get("model"),
                              description=description)
            if not llm_report["ready"]:
                llm_report["error"] = "No model credentials configured (see docs/agent/dependencies.md)."
        except Exception as exc:
            llm_report["error"] = _short(exc)
    report["llm"] = llm_report

    report["real_pipeline_ready"] = (
        all(m["ready"] for n, m in modules.items() if n in REQUIRED)
        and corpus_report["ready"] and llm_report["ready"] and report["participant_package"]["ready"]
    )
    report["fixture_pipeline_ready"] = True
    return report


def package_case(settings: Settings, case_id: str) -> dict[str, Any]:
    """Input files of a case shipped in the participant package. expected.json is never read here."""
    case_dir = settings.cases_dir / case_id
    if not case_id or "/" in case_id or not case_dir.is_dir():
        raise KeyError(case_id)
    spec = case_dir / "spec.md"
    drawings = sorted(p for p in case_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    return {
        "case_id": case_id,
        "spec_path": spec if spec.is_file() else None,
        "drawing_paths": drawings,
        "has_expected": (case_dir / "expected.json").is_file(),
    }


def load_index(settings: Settings) -> list[dict[str, Any]]:
    return json.loads(Path(settings.index_path).read_text(encoding="utf-8"))
