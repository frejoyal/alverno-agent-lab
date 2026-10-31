"""Evaluation suite: gold set, metrics, runner and release gate."""

from alverno_lab.evals.dataset import DatasetError, GoldItem, load_dataset
from alverno_lab.evals.runner import EvalReport, ItemResult, run_eval, write_report
from alverno_lab.evals.thresholds import ThresholdResult, check_thresholds

__all__ = [
    "DatasetError",
    "EvalReport",
    "GoldItem",
    "ItemResult",
    "ThresholdResult",
    "check_thresholds",
    "load_dataset",
    "run_eval",
    "write_report",
]
