"""Importing this package registers all built-in criteria.

Add your own criteria in a new module and import it here (or anywhere before
`build_criteria` runs) so its @register decorators execute.
"""
from . import builtins  # noqa: F401  (side effect: registers built-ins)
from .base import Contribution, Criterion, FilterCriterion, ScoreCriterion, Verdict
from .registry import available, build_criteria, register

__all__ = [
    "Contribution", "Criterion", "FilterCriterion", "ScoreCriterion", "Verdict",
    "available", "build_criteria", "register",
]
