import os
import time
from pathlib import Path

import pytest

from certivity.api.settings import DEFAULT_ENTRY_POINTS, REPO_ROOT, Settings


def _participant_package() -> Path | None:
    candidates = [os.environ.get("CERTIVITY_PARTICIPANT_PACKAGE"), REPO_ROOT / "data" / "participant-package",
                  REPO_ROOT.parent / "industrial-ai-hackathon"]
    for c in candidates:
        if c and (Path(c) / "regulations" / "index.json").is_file():
            return Path(c)
    return None


@pytest.fixture
def package() -> Path:
    path = _participant_package()
    if path is None:
        pytest.skip("participant package not found; set CERTIVITY_PARTICIPANT_PACKAGE")
    return path


@pytest.fixture
def settings(tmp_path, package) -> Settings:
    return Settings(
        runs_dir=tmp_path / "runs", participant_package=package, corpus_dir=tmp_path / "corpus",
        pipeline_mode="fixture", default_team_id="team-test", max_revision_rounds=2, run_time_budget_s=600,
        entry_points=dict(DEFAULT_ENTRY_POINTS),
    )


def wait_finished(client, run_id: str, timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/runs/{run_id}").json()
        if status["finished"]:
            return status
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish: {status}")
