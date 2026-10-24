"""The fast and full extraction modes, and the limits they set.

Fast mode is a bounded reading of the same corpus with the same prompts and the
same citation checks. These assert the two properties that make it safe to fall
back to: it reaches the submission stage, and it does not quietly narrow the set
of regulations, because the score weights recall and applicability is the part
that already works.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from certivity.api.app import create_app
from certivity.api.settings import EXTRACTION_MODES, load_settings
from certivity.contracts import RunConfig
from tests.conftest import wait_finished


def _clear(monkeypatch) -> None:
    for name in (
        "CERTIVITY_EXTRACTION_MODE", "CERTIVITY_MAX_SECTIONS_TOTAL",
        "CERTIVITY_MAX_MODEL_CALLS", "CERTIVITY_MAX_REVISION_ROUNDS",
        "CERTIVITY_PASSAGES_PER_BATCH", "CERTIVITY_MAX_PARALLEL_SECTIONS",
        "CERTIVITY_MAX_REFERENCE_DEPTH", "CERTIVITY_MAX_BATCHES_PER_SECTION",
    ):
        monkeypatch.delenv(name, raising=False)


def test_full_mode_sets_no_extra_limits(monkeypatch):
    """The default must stay the comprehensive pipeline, unchanged."""
    _clear(monkeypatch)
    settings = load_settings()
    assert settings.extraction_mode == "full"
    assert settings.run_config_overrides == {}
    assert settings.max_revision_rounds == 2


def test_fast_mode_bounds_the_work_without_narrowing_the_corpus(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "fast")
    overrides = load_settings().run_config_overrides

    assert overrides["max_sections_total"] > 0
    assert overrides["max_reference_depth"] == 0, "the second pass per batch is the cost"
    assert load_settings().max_revision_rounds == 0
    # The things that would cost recall are deliberately not set.
    assert overrides.get("max_documents") is None
    assert overrides["include_uncertain_documents"] is True, (
        "dropping uncertain regulations would trade away the applicability result"
    )


def test_every_override_is_a_real_run_config_field(monkeypatch):
    """A profile naming a field RunConfig does not have would silently do nothing."""
    _clear(monkeypatch)
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "fast")
    fields = set(RunConfig.__dataclass_fields__)
    assert set(load_settings().run_config_overrides) <= fields


def test_an_explicit_environment_value_beats_the_profile(monkeypatch):
    """A benchmark has to be able to vary one dimension and hold the rest."""
    _clear(monkeypatch)
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "fast")
    monkeypatch.setenv("CERTIVITY_MAX_SECTIONS_TOTAL", "17")
    monkeypatch.setenv("CERTIVITY_MAX_PARALLEL_SECTIONS", "2")
    overrides = load_settings().run_config_overrides
    assert overrides["max_sections_total"] == 17
    assert overrides["max_parallel_sections"] == 2


def test_an_unlimited_override_removes_the_cap(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "fast")
    monkeypatch.setenv("CERTIVITY_MAX_SECTIONS_TOTAL", "none")
    assert load_settings().run_config_overrides["max_sections_total"] is None


def test_an_unknown_mode_is_refused_rather_than_ignored(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CERTIVITY_EXTRACTION_MODE", "turbo")
    with pytest.raises(ValueError, match="CERTIVITY_EXTRACTION_MODE"):
        load_settings()
    assert EXTRACTION_MODES == ("full", "fast")


@pytest.mark.parametrize("mode", EXTRACTION_MODES)
def test_both_modes_reach_a_validated_submission(settings, mode):
    """End to end through the application, with the fixture components."""
    configured = dataclasses.replace(
        settings,
        extraction_mode=mode,
        run_config_overrides={"max_sections_total": 40} if mode == "fast" else {},
        max_revision_rounds=0 if mode == "fast" else 2,
    )
    with TestClient(create_app(configured)) as client:
        run = client.post(
            "/api/runs", data={"package_case": "dev-rear-lamp", "team_id": "team-15"}
        ).json()
        status = wait_finished(client, run["run_id"])
        assert status["state"] in ("completed", "partial"), status["errors"]
        manifest = client.get(f"/api/runs/{run['run_id']}").json()
        assert manifest.get("extraction_mode") == mode, (
            "a run must record which mode produced it"
        )
        submission = client.get(f"/api/runs/{run['run_id']}/submission")
        assert submission.status_code == 200
        body = submission.json()
        # The export carries the team id the application is configured with, not
        # whatever a form posted, so a run made before the real id was set still
        # exports correctly once it is.
        assert body["team_id"] == configured.default_team_id
        assert body["case_id"] == "dev-rear-lamp"
        for requirement in body["requirements"]:
            source = requirement["source"]
            # Version and page must survive the whole pipeline, in every mode.
            assert source["regulation"] and source["version"]
            assert isinstance(source["page"], int) and source["page"] > 0
