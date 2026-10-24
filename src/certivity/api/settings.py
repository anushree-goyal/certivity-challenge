"""Application configuration, read from environment variables (and an optional .env file).

Component entry points are configured as "module:attribute" strings so the app can bind to the
real agent/corpus/quality modules without editing them. Defaults are the planned locations;
confirm them with each owner at the integration checkpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing environment wins."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _path(name: str, default: str) -> Path:
    p = Path(os.environ.get(name, default)).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    runs_dir: Path
    participant_package: Path
    corpus_dir: Path
    pipeline_mode: str  # "real" or "fixture"
    default_team_id: str
    max_revision_rounds: int
    run_time_budget_s: int
    max_model_calls: int | None  # None: the contracts' RunConfig default
    max_review_calls: int | None = None  # review's own budget; None: same as max_model_calls
    entry_points: dict[str, str] = field(default_factory=dict)
    extraction_mode: str = "full"  # "full" or "fast"
    run_config_overrides: dict[str, object] = field(default_factory=dict)

    @property
    def index_path(self) -> Path:
        return self.participant_package / "regulations" / "index.json"

    @property
    def cases_dir(self) -> Path:
        return self.participant_package / "cases"


DEFAULT_ENTRY_POINTS = {
    "open_corpus": "certivity.corpus:open_corpus",
    "bootstrap_corpus": "certivity.corpus:bootstrap",
    "corpus_status": "certivity.corpus:corpus_status",
    "create_llm": "certivity.llm:create_llm",
    "run_extraction": "certivity.agent:run_extraction",
    "revise_extraction": "certivity.agent:revise_extraction",
    "review_requirements": "certivity.quality:review_requirements",
    "build_submission": "certivity.quality:build_submission",
    "build_submission_detailed": "certivity.quality:build_submission_detailed",
    "validate_submission": "certivity.quality:validate_submission",
    "evaluate_case": "certivity.evaluation:evaluate_case",
    "make_run_input": "certivity.contracts:make_run_input",
    "run_config_record": "certivity.contracts:RunConfig",
}


# --------------------------------------------------------------------------- #
# Extraction modes
#
# Two ways to spend a fixed amount of wall-clock time on the same corpus.
#
# "full" reads every section of every selected document, follows the references
# it finds and revises against review findings. It is the higher-recall method
# and the one to use when there is time for it.
#
# "fast" exists because a measured full run completed 15 of 891 sections in 31
# minutes - about 30 hours for the whole plan - against a five-hour window. It
# keeps the same corpus, the same prompts, the same citation checks and the same
# applicability decision, and buys speed from four places only:
#
#   * a cap on how many sections are read, applied after prioritisation, so what
#     is dropped is the tail least likely to state a requirement;
#   * larger batches, so one section is usually one call;
#   * no reference-following second pass over each batch;
#   * no revision rounds.
#
# What it deliberately does NOT do is narrow the set of regulations. The score
# weights recall over precision, and applicability already selects the right
# documents: on the worked example it found all four of the expected regulations.
# Reading fewer of them to save time would trade away the part that is working.
# --------------------------------------------------------------------------- #

EXTRACTION_MODES = ("full", "fast")

_FAST_PROFILE: dict[str, object] = {
    "max_sections_total": 240,
    "max_reference_depth": 0,
    "passages_per_batch": 20,
    "max_batches_per_section": 2,
    "max_parallel_sections": 8,
    "max_model_calls": 700,
    "include_uncertain_documents": True,
}
"""Measured starting point, not a tuned optimum. Every value is overridable by
its own environment variable so a benchmark can move one at a time."""

_RUN_CONFIG_ENV = {
    "max_sections_total": "CERTIVITY_MAX_SECTIONS_TOTAL",
    "max_sections_per_document": "CERTIVITY_MAX_SECTIONS_PER_DOCUMENT",
    "max_reference_depth": "CERTIVITY_MAX_REFERENCE_DEPTH",
    "passages_per_batch": "CERTIVITY_PASSAGES_PER_BATCH",
    "max_batches_per_section": "CERTIVITY_MAX_BATCHES_PER_SECTION",
    "max_parallel_sections": "CERTIVITY_MAX_PARALLEL_SECTIONS",
    "max_model_calls": "CERTIVITY_MAX_MODEL_CALLS",
    "request_timeout_s": "CERTIVITY_REQUEST_TIMEOUT_S",
    "max_retries": "CERTIVITY_MAX_RETRIES",
}


def _run_config_overrides(mode: str) -> dict[str, object]:
    """The RunConfig fields this mode and this environment set.

    An explicit environment variable always beats the mode profile, so a
    benchmark can hold a profile and vary one dimension.
    """
    overrides: dict[str, object] = dict(_FAST_PROFILE) if mode == "fast" else {}
    for field_name, var in _RUN_CONFIG_ENV.items():
        raw = os.environ.get(var)
        if raw is None or not raw.strip():
            continue
        text = raw.strip()
        if text.lower() in ("none", "unlimited", ""):
            overrides[field_name] = None
            continue
        try:
            overrides[field_name] = (
                float(text) if field_name == "request_timeout_s" else int(text)
            )
        except ValueError:
            continue
    return overrides


PLACEHOLDER_TEAM_IDS = {"team-07", "team-demo", "team-test", "your-team-id"}


def team_id_problem(team_id: str) -> str | None:
    """Why the configured team ID cannot be trusted for exports, or None when it looks real."""
    value = (team_id or "").strip()
    if not value:
        return "CERTIVITY_TEAM_ID is not set; exports are blocked until it is (team id from hackathon.certivity.io)."
    if value.lower() in PLACEHOLDER_TEAM_IDS:
        return f"CERTIVITY_TEAM_ID is a placeholder ({value!r}); set your real team id from hackathon.certivity.io."
    return None


def load_settings() -> Settings:
    _load_dotenv(REPO_ROOT / ".env")
    entry_points = {
        name: os.environ.get(f"CERTIVITY_EP_{name.upper()}", default)
        for name, default in DEFAULT_ENTRY_POINTS.items()
    }
    mode = os.environ.get("CERTIVITY_PIPELINE_MODE", "real").lower()
    if mode not in ("real", "fixture"):
        raise ValueError(f"CERTIVITY_PIPELINE_MODE must be 'real' or 'fixture', got {mode!r}")
    extraction_mode = os.environ.get("CERTIVITY_EXTRACTION_MODE", "full").strip().lower() or "full"
    if extraction_mode not in EXTRACTION_MODES:
        raise ValueError(
            f"CERTIVITY_EXTRACTION_MODE must be one of {EXTRACTION_MODES}, got {extraction_mode!r}"
        )
    overrides = _run_config_overrides(extraction_mode)
    # Fast mode skips revision unless the operator asks for it back: a revision
    # round re-reads drafts with the model, which is the opposite of the trade
    # this mode is making.
    default_rounds = 0 if extraction_mode == "fast" else 2
    return Settings(
        runs_dir=_path("CERTIVITY_RUNS_DIR", "runs"),
        participant_package=_path("CERTIVITY_PARTICIPANT_PACKAGE", "data/participant-package"),
        # CERTIVITY_CORPUS_CACHE is the corpus module's own name for the same folder.
        corpus_dir=_path("CERTIVITY_CORPUS_DIR", os.environ.get("CERTIVITY_CORPUS_CACHE", "data/cache/corpus")),
        pipeline_mode=mode,
        default_team_id=os.environ.get("CERTIVITY_TEAM_ID", ""),
        max_revision_rounds=_int("CERTIVITY_MAX_REVISION_ROUNDS", default_rounds),
        run_time_budget_s=_int("CERTIVITY_RUN_TIME_BUDGET_S", 3 * 3600),
        # The adapter's call budget and the RunConfig's must agree, so both come
        # from the same resolved value: the environment variable if set, else
        # whatever the extraction mode's profile asks for.
        max_model_calls=overrides.get("max_model_calls"),  # type: ignore[arg-type]
        max_review_calls=int(os.environ["CERTIVITY_MAX_REVIEW_CALLS"]) if os.environ.get("CERTIVITY_MAX_REVIEW_CALLS") else None,
        entry_points=entry_points,
        extraction_mode=extraction_mode,
        run_config_overrides=overrides,
    )
