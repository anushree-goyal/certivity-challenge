"""Checks the owners' entry points against the planned call signatures once their modules are merged.

Each case is skipped until its module exists, so this is meant to fail loudly at integration time.
"""

import inspect

import pytest

from certivity.api.components import ComponentError, resolve
from certivity.api.settings import DEFAULT_ENTRY_POINTS, load_settings

PLANNED = {
    "run_extraction": ["run_input", "corpus", "llm", "emit_event"],
    "revise_extraction": ["run_input", "draft", "findings", "corpus", "llm", "emit_event"],
    "review_requirements": ["run_input", "draft", "corpus", "llm", "emit_event"],
    "build_submission": ["run_input", "draft", "report", "corpus"],
    "build_submission_detailed": ["run_input", "draft", "report", "corpus"],
    "validate_submission": ["submission", "participant_package_path"],
    "evaluate_submission": ["submission_path", "expected_path"],
}
CORPUS_METHODS = ["list_documents", "read_scope", "list_sections", "read_section", "get_passages",
                  "resolve_reference", "get_page_asset"]
# Keywords the app passes to contracts.make_run_input (keyword-only).
RUN_INPUT_KWARGS = ["run_id", "team_id", "case_id", "specification_text", "drawing_paths", "input_hash",
                    "corpus_snapshot_id"]


def _load(name):
    try:
        return resolve(DEFAULT_ENTRY_POINTS[name])
    except ComponentError as exc:
        pytest.skip(f"{name} not merged yet: {exc}")


@pytest.mark.parametrize("name", sorted(PLANNED))
def test_entry_point_accepts_planned_positional_arguments(name):
    func = _load(name)
    params = [p for p in inspect.signature(func).parameters.values()
              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    has_varargs = any(p.kind == p.VAR_POSITIONAL for p in inspect.signature(func).parameters.values())
    assert has_varargs or len(params) >= len(PLANNED[name]), (
        f"{name}{inspect.signature(func)} cannot take the app's {len(PLANNED[name])} positional arguments {PLANNED[name]}")


def test_make_run_input_accepts_app_keywords():
    params = inspect.signature(_load("make_run_input")).parameters
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return
    missing = [k for k in RUN_INPUT_KWARGS if k not in params]
    assert not missing, f"make_run_input lacks keywords the app sends: {missing}. Align contracts.py with api/pipeline.py."


def test_create_llm_accepts_cache_dir_and_describes_itself():
    create_llm = _load("create_llm")
    assert "cache_dir" in inspect.signature(create_llm).parameters
    description = create_llm().describe()
    assert "credentials_configured" in description
    assert not any("key" in k.lower() and isinstance(v, str) and len(v) > 20 for k, v in description.items())


def test_corpus_reader_has_planned_methods():
    open_corpus = _load("open_corpus")
    try:
        corpus = open_corpus(load_settings().corpus_dir)  # the real snapshot, once built
    except Exception as exc:
        pytest.skip(f"corpus module merged but no snapshot available: {exc}")
    missing = [m for m in CORPUS_METHODS if not callable(getattr(corpus, m, None))]
    assert not missing, f"CorpusReader lacks {missing}"
    assert getattr(corpus, "snapshot_id", None), "CorpusReader should expose snapshot_id"
