"""Evaluation (Teammate 3). Only this package may read the golden answer."""
from .runs import (candidate_id, compare_candidates, list_candidates, load_candidate,
                   save_candidate, write_report)
from .report import EvaluationDetails
from .scoring import answer_file_for, evaluate_case, evaluate_submission, evaluate_submission_detailed

__all__ = ["evaluate_case", "answer_file_for", "evaluate_submission", "evaluate_submission_detailed", "EvaluationDetails", "save_candidate", "load_candidate", "list_candidates",
           "compare_candidates", "candidate_id", "write_report"]
