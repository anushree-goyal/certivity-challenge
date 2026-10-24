"""EvaluationReport. Uses the shared contract if it exists, else a temporary stand-in."""
from __future__ import annotations

from dataclasses import dataclass, field

try:  # pragma: no cover
    from certivity.contracts import EvaluationReport  # type: ignore
    USING_SHARED_CONTRACTS = True
except Exception:  # noqa: BLE001
    USING_SHARED_CONTRACTS = False

    @dataclass
    class EvaluationReport:  # fallback
        scope: str
        counts: dict
        regulation_level: dict          # precision/recall/f1 (own computation)
        page_level: dict                # precision/recall/f1 on (regulation, page) pairs
        error_categories: dict          # category -> list of {regulation, page, note}
        official_local_scorer: dict | None = None   # raw wrapped-scorer result, if it ran
        semantic_score: float | None = None          # platform metric: None until measured
        platform_score: float | None = None
        notes: list = field(default_factory=list)
