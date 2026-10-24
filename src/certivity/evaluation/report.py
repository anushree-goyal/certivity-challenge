"""Evaluation results. The shared contract's EvaluationReport is the summary; EvaluationDetails
keeps what that flat record cannot hold (per-item misses, raw official scorer output)."""
from __future__ import annotations

from dataclasses import dataclass, field

from certivity.contracts import EvaluationReport


@dataclass
class EvaluationDetails:
    report: EvaluationReport          # the contract summary (what the app shows)
    scope: str
    metrics_source: str               # "official score_local.py" or "own computation"
    own_regulation_level: dict        # precision/recall/f1 + counts, own computation
    own_page_level: dict              # on (regulation, version, page), exact matches only
    error_items: dict                 # category -> list of {regulation, version, page, note}
    official_local_scorer: dict | None = None   # raw wrapped-scorer result, if it ran
    notes: list = field(default_factory=list)


__all__ = ["EvaluationReport", "EvaluationDetails"]
