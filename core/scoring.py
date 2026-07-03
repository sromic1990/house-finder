"""Turn a set of listings + criteria into a ranked leaderboard.

Rules:
  * Any failed hard filter -> the listing is EXCLUDED (with the reason kept for
    debugging / an optional "rejected" view).
  * The score is the weighted mean of applicable scorer contributions,
    rescaled to 0..100. Scorers that can't judge a listing (missing data) are
    dropped from BOTH numerator and denominator, so a listing is never
    penalized merely for a source omitting a field.
  * Ties break by €/m² (cheaper first), then price, then uid for stability.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .criteria.base import Criterion


def _dedup_key(listing):
    """Identity for cross-source de-dup: same street address + size + rooms is
    the same property posted on both portals (different apartments at one address
    differ in size/rooms, so they stay separate)."""
    addr = re.sub(r"[^a-z0-9äöå]", "", (listing.address or "").lower())
    if not addr:
        return ("uid", listing.uid)   # no address -> never dedup
    return (addr, listing.size_m2, listing.rooms)


def _dedup(ranked: list) -> list:
    seen, out = set(), []
    for r in ranked:
        key = _dedup_key(r.listing)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


@dataclass
class ScoredListing:
    listing: Any
    score: float                      # 0..100
    rank: int = 0
    priority: float = 0.0             # ranking tier (higher = sorted first)
    contributions: list = field(default_factory=list)  # list[Contribution]
    excluded: bool = False
    exclude_reason: str = ""
    passed_filters: list = field(default_factory=list)  # list[(title, reason)]

    @property
    def explanation(self) -> list[str]:
        return [c.label for c in self.contributions if c.applicable]


def _evaluate_one(listing, filters, scorers) -> ScoredListing:
    passed = []
    for f in filters:
        if not f.applies(listing):
            continue
        v = f.check(listing)
        passed.append((f.title, v.reason))
        if not v.passed:
            return ScoredListing(listing=listing, score=0.0, excluded=True,
                                 exclude_reason=f"{f.title}: {v.reason}",
                                 passed_filters=passed)

    contribs, num, den = [], 0.0, 0.0
    prio, prio_w = 0.0, 0.0
    for s in scorers:
        if not s.applies(listing):
            continue
        c = s.score(listing)
        contribs.append(c)
        if not c.applicable:
            continue
        if getattr(s, "is_priority", False):   # tier, not part of the 0..100 score
            prio += c.raw * c.weight
            prio_w += c.weight
        else:
            num += c.weighted
            den += c.weight

    score = round(100.0 * num / den, 1) if den else 0.0
    priority = round(prio / prio_w, 4) if prio_w else 0.0
    return ScoredListing(listing=listing, score=score, priority=priority,
                         contributions=contribs, passed_filters=passed)


def rank_listings(listings, criteria: list[Criterion]) -> list[ScoredListing]:
    """Return ALL listings scored; kept ones ranked, excluded ones flagged."""
    filters = [c for c in criteria if c.kind == "filter"]
    scorers = [c for c in criteria if c.kind == "score"]

    results = [_evaluate_one(l, filters, scorers) for l in listings]
    kept = [r for r in results if not r.excluded]

    def sort_key(r: ScoredListing):
        ppm2 = r.listing.price_per_m2 or float("inf")
        price = r.listing.price or float("inf")
        # priority tier first (own land above leased), then score, then value
        return (-r.priority, -r.score, ppm2, price, r.listing.uid)

    kept.sort(key=sort_key)
    kept = _dedup(kept)            # collapse the same property from both portals
    for i, r in enumerate(kept, start=1):
        r.rank = i
    return kept


def leaderboard(listings, criteria, include_excluded=False):
    ranked = rank_listings(listings, criteria)
    if include_excluded:
        filters = [c for c in criteria if c.kind == "filter"]
        scorers = [c for c in criteria if c.kind == "score"]
        excluded = [_evaluate_one(l, filters, scorers) for l in listings]
        excluded = [r for r in excluded if r.excluded]
        return ranked, excluded
    return ranked
