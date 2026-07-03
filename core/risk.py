"""Heuristic risk flags surfaced on each listing.

These are INDICATIVE estimates from listing data — not a bank's decision nor a
professional condition survey. Two flags:

  renovation_risk : how likely a big renovation/inspection cost lands in the next
                    ~5 years. Driven mainly by building age (the Finnish
                    putkiremontti / roof / facade cycle peaks at ~40-50 years),
                    bumped by detached ownership and poor energy class.

  bank_risk       : how a mortgage bank might view the property as collateral for
                    a FIRST-TIME buyer with little savings (but stable, good
                    income). Income/DTI isn't the worry here — collateral quality
                    and the chance of needing cash the buyer lacks are: leased
                    plot, paying over the area €/m², an aging building, detached
                    upkeep.

Each returns {"score": 0..1, "level": "low|medium|high", "reasons": [str, ...]}.
"""
from __future__ import annotations

DETACHED = ("omakotitalo", "erillistalo")


def _level(score: float, med: float, hi: float) -> str:
    return "high" if score >= hi else "medium" if score >= med else "low"


def renovation_risk(listing, year_now: int) -> dict:
    y = listing.year_built
    detached = listing.property_type in DETACHED
    energy = (listing.features.get("energy_class") or "").upper()
    reasons = []
    if y:
        age = year_now - y
        if age >= 45:
            base = 0.85
            reasons.append(f"Built {y} (~{age} yr) — deep in the plumbing/roof/facade renovation window")
        elif age >= 38:
            base = 0.7
            reasons.append(f"Built {y} (~{age} yr) — entering the major-renovation (putkiremontti) age")
        elif age >= 28:
            base = 0.5
            reasons.append(f"Built {y} (~{age} yr) — some major renovations may fall within a few years")
        elif age >= 18:
            base = 0.3
            reasons.append(f"Built {y} (~{age} yr) — mostly minor upkeep expected near-term")
        else:
            base = 0.12
            reasons.append(f"Built {y} (~{age} yr) — newer building, low near-term renovation need")
    else:
        base = 0.45
        reasons.append("Build year not stated — condition/renovation timing unclear")
    if detached:
        base = min(1.0, base + 0.1)
        reasons.append("Detached house — renovation costs fall entirely on the owner, as a lump sum")
    if energy in ("E", "F", "G"):
        base = min(1.0, base + 0.1)
        reasons.append(f"Energy class {energy} — possible energy-efficiency upgrades")
    return {"score": round(base, 2), "level": _level(base, 0.4, 0.66), "reasons": reasons}


def bank_risk(listing, median_ppm2: float | None, reno_level: str) -> dict:
    score = 0.0
    reasons = []
    land = listing.features.get("land_ownership")
    if land == "rented":
        score += 0.45
        reasons.append("Leased plot (vuokratontti) — weaker collateral and an ongoing plot rent the bank counts against you")
    elif land == "optional_rental":
        score += 0.25
        reasons.append("Optional-rental plot — partial plot-rent exposure")
    if reno_level == "high":
        score += 0.25
        reasons.append("Aging building may soon need renovation cash — a stretch with little savings")
    ppm2 = listing.price_per_m2
    if ppm2 and median_ppm2 and ppm2 > 1.25 * median_ppm2:
        score += 0.2
        reasons.append("Price/m² well above the area norm — risk of borrowing more than the market value")
    if listing.property_type in DETACHED:
        score += 0.1
        reasons.append("Detached house — no housing company to share surprise upkeep costs")
    score = min(1.0, score)
    lvl = _level(score, 0.3, 0.55)
    if lvl == "low" and not reasons:
        reasons.append("Owned plot, market-rate pricing — straightforward collateral")
    return {"score": round(score, 2), "level": lvl, "reasons": reasons}
