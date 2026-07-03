"""Criteria engine — the extensible heart of the ranking system.

Two kinds of criteria:

  * FilterCriterion  -> pass/fail. A failed hard requirement EXCLUDES the
                        listing from the leaderboard (e.g. "must have a
                        private sauna").
  * ScoreCriterion   -> returns a normalized 0..1 quality for the listing on
                        this one dimension, combined with a config weight into
                        the final ranking score (e.g. parking type, price).

To add a criterion later you only:
  1. subclass FilterCriterion or ScoreCriterion,
  2. give it a unique `key` and decorate with @register,
  3. add a matching block under `criteria:` in config.yaml.

Nothing else in the codebase needs to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---- Result types ---------------------------------------------------------
@dataclass
class Verdict:
    """Outcome of a hard filter."""
    passed: bool
    reason: str = ""


@dataclass
class Contribution:
    """Outcome of a soft scorer for one listing on one dimension."""
    raw: float                 # 0..1 (clamped); how good this listing is here
    weight: float              # relative importance from config
    label: str = ""            # human-readable explanation for the UI
    applicable: bool = True    # False => data missing; excluded from the mean

    @property
    def weighted(self) -> float:
        return max(0.0, min(1.0, self.raw)) * self.weight


# ---- Base classes ---------------------------------------------------------
class Criterion(ABC):
    key: str = ""              # unique id; must match a config block key
    kind: str = ""             # "filter" | "score"
    title: str = ""            # shown in the UI / emails

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.weight = float(self.config.get("weight", 1.0))
        # A scorer flagged priority acts as a ranking TIER: listings are sorted
        # by priority first, then by the weighted score within each tier.
        self.is_priority = bool(self.config.get("priority", False))

    def applies(self, listing) -> bool:
        """Override to skip listings this criterion can't judge (missing data)."""
        return True


class FilterCriterion(Criterion):
    kind = "filter"

    @abstractmethod
    def check(self, listing) -> Verdict: ...


class ScoreCriterion(Criterion):
    kind = "score"

    @abstractmethod
    def score(self, listing) -> Contribution: ...
