"""The two extraction modes, and what each is allowed to trade away.

"fast" exists because a measured full run completed 15 of 891 sections in 31
minutes. It is allowed to read fewer sections and to skip reference-following
and revision. It is not allowed to narrow the set of regulations: the score
weights recall over precision, and applicability already selects the right
documents, so dropping documents would trade away the part that works.
"""

from __future__ import annotations

import pytest

from certivity.api.settings import EXTRACTION_MODES, load_settings
from certivity.contracts import RunConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "CERTIVITY_EXTRACTION_MODE", "CERTIVITY_MAX_SECTIONS_TOTAL",
        "CERTIVITY_MAX_MODEL_CALLS", "CERTIVITY_MAX_REVISION_ROUNDS",
        "CERTIVITY_PASSAGES_PER_BATCH", "CERTIVITY_MAX_REFERENCE_DEPTH",
        "CERTIVITY_MAX_PARALLEL_SECTIONS", "CERTIVITY_MAX_BATCHES_PER_SECTION",
        "CERTIVITY_REQUEST_TIMEOUT_S", "CERTIVITY_MAX_RETRIES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_full_is_the_default_and_changes_no_limit():
    """The comprehensive pipeline must stay exactly as it was."""
    settings = load_settings()
    assert settings.extraction_mode == "full"
    assert settings.run_config_overrides == {}
    assert settings.max_model_calls is None
    assert settings.max_revision_rounds == 2


def test_fast_bounds_the_work_without_dropping_documents(monkeypatch):
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "fast")
    settings = load_settings()
    overrides = settings.run_config_overrides

    assert settings.extraction_mode == "fast"
    assert overrides["max_sections_total"] > 0
    assert overrides["max_reference_depth"] == 0, "the second pass per batch is the cost"
    assert settings.max_revision_rounds == 0
    # The recall-preserving half of the bargain.
    assert overrides["include_uncertain_documents"] is True
    assert "max_documents" not in overrides
    assert "max_sections_per_document" not in overrides


def test_fast_overrides_are_all_real_run_config_fields(monkeypatch):
    """A profile naming a field RunConfig does not have would silently do nothing."""
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "fast")
    overrides = load_settings().run_config_overrides
    unknown = set(overrides) - set(RunConfig.__dataclass_fields__)
    assert unknown == set()
    RunConfig(**overrides)  # and it constructs


def test_an_environment_variable_beats_the_mode_profile(monkeypatch):
    """A benchmark must be able to hold a profile and move one dimension."""
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "fast")
    monkeypatch.setenv("CERTIVITY_MAX_SECTIONS_TOTAL", "17")
    monkeypatch.setenv("CERTIVITY_MAX_PARALLEL_SECTIONS", "2")
    overrides = load_settings().run_config_overrides
    assert overrides["max_sections_total"] == 17
    assert overrides["max_parallel_sections"] == 2


def test_a_cap_can_be_removed_by_name(monkeypatch):
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "fast")
    monkeypatch.setenv("CERTIVITY_MAX_SECTIONS_TOTAL", "none")
    assert load_settings().run_config_overrides["max_sections_total"] is None


def test_the_call_budget_is_one_number_for_adapter_and_run_config(monkeypatch):
    """Two budgets that disagree is a run that stops for an unexplained reason."""
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "fast")
    settings = load_settings()
    assert settings.max_model_calls == settings.run_config_overrides["max_model_calls"]

    monkeypatch.setenv("CERTIVITY_MAX_MODEL_CALLS", "42")
    settings = load_settings()
    assert settings.max_model_calls == 42
    assert settings.run_config_overrides["max_model_calls"] == 42


def test_an_unknown_mode_is_refused_rather_than_silently_full(monkeypatch):
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "turbo")
    with pytest.raises(ValueError, match="CERTIVITY_EXTRACTION_MODE"):
        load_settings()
    assert EXTRACTION_MODES == ("full", "fast")


def test_fast_mode_revision_can_be_asked_for_back(monkeypatch):
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "fast")
    monkeypatch.setenv("CERTIVITY_MAX_REVISION_ROUNDS", "1")
    assert load_settings().max_revision_rounds == 1


def test_model_verification_fails_loudly_without_credentials(monkeypatch):
    """A configured key is not a usable one, and "ready" must not claim it is.

    An exhausted credit balance arrives as a 400 on the first real request, so
    readiness that only checks for a credential reports a usable account for one
    that cannot make a single call. This asserts the verification says so rather
    than raising, because it runs as a go/no-go check.
    """
    from certivity.cli import _verify_model_usable

    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "pathlib.Path.is_dir", lambda self: False, raising=False
    )

    result = _verify_model_usable()
    assert result["usable"] is False
    assert result["reason"] == "account_configuration"
    assert "credential" in result["detail"].lower()
