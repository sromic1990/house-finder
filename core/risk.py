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

import re

DETACHED = ("omakotitalo", "erillistalo")

# --- renovation-text matchers -------------------------------------------------
_RENEW = r"(?:uusit\w*|uusim\w*|uusinta\w*|saneera\w*|peruskorja\w*|remontoit\w*)"
_PIPE = r"(?:putki\w*|putkist\w*|viemär\w*|käyttövesi\w*|linjast\w*)"
# A COMPLETED pipe renovation: the compound word, or a pipe noun paired with a
# *renewal* verb. A plain repair ("viemärin kaadon korjaus") must NOT qualify.
_PLUMB_DONE = re.compile(
    r"putkiremon\w*|linjasaneera\w*|" + _PIPE + r"\s+" + _RENEW
    + r"|" + _RENEW + r"\s+" + _PIPE, re.I)
# Planned pipe work — any mention in the upcoming list is a strong signal.
_PLUMB_PLAN = re.compile(r"putkiremon\w*|linjasaneera\w*|(?:putki|viemär|käyttövesi)\w*", re.I)
_MAJOR_PLAN = re.compile(r"julkisivu\w*|vesikat\w*|(?:^| )katto\w*|ikkun\w*|salaoj\w*|parveke\w*", re.I)
# A history of ANY damage is a red flag, not reassurance: water/moisture/mould/
# rot, fire, frost-heave, cracks, settling, or a bare "…vaurio" / "…vahinko".
_DAMAGE = re.compile(
    r"\w*vauri\w*|\w*vahin\w*|home(?:htu|vaur)\w*|laho\w*|"
    r"routi\w*|halkeam\w*|halkeil\w*|painum\w*|mikrobi\w*", re.I)
# "…ei havaittu / ei todettu / ei merkkejä vaurioita" — a clean report, not damage.
_NO_DAMAGE = re.compile(
    r"ei\s+(?:ole\s+|ollut\s+)?(?:havait\w*|todet\w*|löyt\w*|merkke\w*|viittei\w*|"
    r"merkkejä\w*)[^.,;:]*", re.I)


def _damage_hits(text: str) -> int:
    return len(_DAMAGE.findall(_NO_DAMAGE.sub(" ", text or "")))


def _level(score: float, med: float, hi: float) -> str:
    return "high" if score >= hi else "medium" if score >= med else "low"


def renovation_risk(listing, year_now: int) -> dict:
    y = listing.year_built
    detached = listing.property_type in DETACHED
    energy = (listing.features.get("energy_class") or "").upper()
    planned = (listing.features.get("reno_planned") or "").lower()
    done = (listing.features.get("reno_done") or "").lower()
    reasons: list[str] = []

    # 1. Age baseline.
    if y:
        age = max(0, year_now - y)   # guard against a future/typo build year
        for thr, val, msg in (
                (45, 0.85, "deep in the plumbing/roof/facade renovation window"),
                (38, 0.70, "entering the major-renovation (putkiremontti) age"),
                (28, 0.50, "some major renovations may fall within a few years"),
                (18, 0.30, "mostly minor upkeep expected near-term"),
                (0, 0.12, "newer building, low near-term renovation need")):
            if age >= thr:
                base, age_msg = val, f"Built {y} (~{age} yr) — {msg}"
                break
    else:
        base, age_msg = 0.45, "Build year not stated — condition/renovation timing unclear"

    # 2. The taloyhtiö's ACTUAL listed renovations override the age guess. A
    #    genuinely completed putkiremontti is the biggest de-risker for an
    #    apartment; for a detached house the pipes are only one owner-borne
    #    system (roof, facade, foundation remain), so it de-risks far less.
    if _PLUMB_PLAN.search(planned):
        base = max(base, 0.85)
        reasons.append("Major plumbing (putkiremontti) is in the taloyhtiö's upcoming plan")
    elif _MAJOR_PLAN.search(planned):
        base = max(base, 0.62)
        reasons.append("A major renovation (facade/roof/windows) is in the upcoming plan")
    elif _PLUMB_DONE.search(done) and not detached:
        base = min(base, 0.30)
        reasons.append("Major plumbing (putkiremontti) already done — that big-ticket item is behind you")
    elif _PLUMB_DONE.search(done) and detached:
        base = min(base, 0.50)
        reasons.append("Pipes already renewed — but the roof, facade and rest still fall on the owner alone")
    else:
        reasons.append(age_msg)

    # 3. A history of ANY damage (water, fire, frost, cracks, settling, mould…)
    #    is a red flag — raise, don't lower. "No damage found" is scrubbed first.
    damage_hits = _damage_hits(done) + _damage_hits(planned)
    if damage_hits:
        base = min(1.0, base + (0.12 if damage_hits == 1 else 0.24))
        reasons.insert(0, "Listing notes " + ("a past" if damage_hits == 1 else "repeated")
                       + " damage (water, structural or other) — inspect the condition report carefully")

    # 4. Structural modifiers.
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
