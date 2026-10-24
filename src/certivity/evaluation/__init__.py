"""Evaluation (Teammate 3). Only this package may read the golden answer."""
from .runs import (candidate_id, compare_candidates, list_candidates, load_candidate,
                   save_candidate, write_report)
from .scoring import evaluate_submission

__all__ = ["evaluate_submission", "save_candidate", "load_candidate", "list_candidates",
           "compare_candidates", "candidate_id", "write_report"]
