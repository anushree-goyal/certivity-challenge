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
    entry_points: dict[str, str] = field(default_factory=dict)

    @property
    def index_path(self) -> Path:
        return self.participant_package / "regulations" / "index.json"

    @property
    def cases_dir(self) -> Path:
        return self.participant_package / "cases"


DEFAULT_ENTRY_POINTS = {
    "open_corpus": "certivity.corpus:open_corpus",
    "bootstrap_corpus": "certivity.corpus:bootstrap",
    "create_llm": "certivity.llm:create_llm",
    "run_extraction": "certivity.agent:run_extraction",
    "revise_extraction": "certivity.agent:revise_extraction",
    "review_requirements": "certivity.quality:review_requirements",
    "build_submission": "certivity.quality:build_submission",
    "validate_submission": "certivity.quality:validate_submission",
    "evaluate_submission": "certivity.evaluation:evaluate_submission",
    "run_input_record": "certivity.contracts:RunInput",
}


def load_settings() -> Settings:
    _load_dotenv(REPO_ROOT / ".env")
    entry_points = {
        name: os.environ.get(f"CERTIVITY_EP_{name.upper()}", default)
        for name, default in DEFAULT_ENTRY_POINTS.items()
    }
    mode = os.environ.get("CERTIVITY_PIPELINE_MODE", "real").lower()
    if mode not in ("real", "fixture"):
        raise ValueError(f"CERTIVITY_PIPELINE_MODE must be 'real' or 'fixture', got {mode!r}")
    return Settings(
        runs_dir=_path("CERTIVITY_RUNS_DIR", "runs"),
        participant_package=_path("CERTIVITY_PARTICIPANT_PACKAGE", "data/participant-package"),
        corpus_dir=_path("CERTIVITY_CORPUS_DIR", "data/cache/corpus"),
        pipeline_mode=mode,
        default_team_id=os.environ.get("CERTIVITY_TEAM_ID", ""),
        max_revision_rounds=_int("CERTIVITY_MAX_REVISION_ROUNDS", 2),
        run_time_budget_s=_int("CERTIVITY_RUN_TIME_BUDGET_S", 3 * 3600),
        entry_points=entry_points,
    )
