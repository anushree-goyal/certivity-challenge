"""Path setup for the agent component's tests (owner: Anushree).

The package layout (``src/``) and the fixture package (``tests/``) both need to
be importable. Teammate 4 owns the root dependency configuration, so these
tests do not assume an installed package and work with a bare ``pytest`` run
from the repository root.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT, ROOT / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from certivity.contracts import RunConfig, RunInput, content_hash  # noqa: E402
from tests.fixtures.contracts import (  # noqa: E402
    InMemoryCorpusReader,
    ScriptedLLMAdapter,
    default_script,
)
from tests.fixtures.contracts import corpus as syn  # noqa: E402


@pytest.fixture
def corpus() -> InMemoryCorpusReader:
    return InMemoryCorpusReader()


@pytest.fixture
def script() -> dict:
    return default_script()


@pytest.fixture
def llm(script: dict) -> ScriptedLLMAdapter:
    return ScriptedLLMAdapter(script=script)


@pytest.fixture
def run_input() -> RunInput:
    return RunInput(
        run_id="run-test-0001",
        team_id="team-test",
        case_id="synthetic-signalling-device",
        corpus_snapshot_id=syn.SNAPSHOT_ID,
        input_hash=content_hash("test-input"),
        specification_text=(
            "Rear signalling device for a passenger car (category M1). Emits red "
            "light through a polycarbonate lens."
        ),
        config=RunConfig(max_model_calls=60, passages_per_batch=4),
    )
