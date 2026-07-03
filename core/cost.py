"""Approximate 5-year cost to own, on a per-month basis.

An INDICATIVE estimate (not an offer or a survey). Three parts:

  mortgage    — monthly payment on a mortgage for the debt-free price, at
                assumed down-payment / rate / term. (Financing the full price
                approximates personal mortgage + any taloyhtiö loan, so we don't
                also add the pääomavastike — that would double-count.)
  charges     — the monthly hoitovastike if scraped from the detail page, else a
                per-m² estimate (detached homes get a running-cost estimate:
                heating, water, insurance, property tax, upkeep).
  renovation  — expected big-renovation spend over the next 5 years, spread per
                month. Driven by the renovation-risk score (which itself is
                informed by the taloyhtiö's listed upcoming renovations).

All assumptions live in DEFAULTS and can be overridden from config.web.cost.
"""
from __future__ import annotations

DEFAULTS = {
    "down_payment_pct": 0.10,     # first-time buyer with little savings
    "mortgage_rate": 0.038,       # ~3.8 % nominal
    "mortgage_years": 25,
    "est_charge_per_m2": 4.5,     # €/m²/month when hoitovastike isn't stated
    "detached_running_per_m2": 5.0,  # running costs for an omakotitalo
    "reno_per_m2": 350,           # €/m² of major renovation, risk-weighted, over 5y
}

DETACHED = ("omakotitalo", "erillistalo")


def _annuity(principal: float, annual_rate: float, years: int) -> float:
    if principal <= 0:
        return 0.0
    r, n = annual_rate / 12, years * 12
    return principal / n if r == 0 else principal * r / (1 - (1 + r) ** -n)


def cost_to_own(listing, reno_score: float, cfg: dict | None = None) -> dict:
    c = {**DEFAULTS, **(cfg or {})}
    price = listing.price or 0
    size = listing.size_m2 or 0
    f = listing.features

    mortgage = _annuity(price * (1 - c["down_payment_pct"]), c["mortgage_rate"], c["mortgage_years"])

    maint = f.get("maintenance")
    if maint is not None:
        charges = maint
    elif listing.property_type in DETACHED:
        charges = size * c["detached_running_per_m2"]
    else:
        charges = size * c["est_charge_per_m2"]

    reno_monthly = (reno_score * c["reno_per_m2"] * size) / 60.0
    monthly = mortgage + charges + reno_monthly
    return {
        "monthly": round(monthly),
        "breakdown": {"mortgage": round(mortgage), "charges": round(charges),
                      "renovation": round(reno_monthly)},
        "five_year": round(monthly * 60),
        "charges_estimated": maint is None,
        "assumptions": {"down_pct": c["down_payment_pct"], "rate": c["mortgage_rate"],
                        "years": c["mortgage_years"]},
    }
