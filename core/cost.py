"""Fixed monthly cost to own — EXCLUDING any mortgage.

The mortgage depends on your down payment, rate and term (your decisions), so
we don't guess it. Instead we surface the costs you can't avoid, so you can size
a mortgage that keeps your total within budget:

  charges     — the monthly maintenance charge (hoitovastike) scraped from the
                listing. For a detached house (no taloyhtiö) it's a per-m²
                running-cost estimate (heating, water, insurance, property tax,
                upkeep), flagged as estimated.
  renovation  — an ESTIMATE of money to set aside monthly for big renovations
                over the next 5 years, weighted by the (renovation-aware) risk
                score. This is the "surprises" line.

Also reported for context (NOT added to the fixed total, because it's financing
you can choose to keep or buy out with a bigger mortgage):
  financing_fee — the taloyhtiö loan payment (pääomavastike / rahoitusvastike).
  charge_total  — total monthly vastike incl. that company loan.
"""
from __future__ import annotations

DEFAULTS = {
    "est_charge_per_m2": 4.5,        # €/m²/month when hoitovastike isn't stated
    "detached_running_per_m2": 5.0,  # running costs for a detached omakotitalo
    "reno_per_m2": 350,              # €/m² of major renovation, risk-weighted, over 5y
}

DETACHED = ("omakotitalo", "erillistalo")


def cost_to_own(listing, reno_score: float, cfg: dict | None = None) -> dict:
    c = {**DEFAULTS, **(cfg or {})}
    size = listing.size_m2 or 0
    f = listing.features

    maint = f.get("maintenance")
    if maint is not None:
        charges, charges_est = maint, False
    elif listing.property_type in DETACHED:
        charges, charges_est = size * c["detached_running_per_m2"], True
    else:
        charges, charges_est = size * c["est_charge_per_m2"], True

    reno_monthly = (reno_score * c["reno_per_m2"] * size) / 60.0
    fixed = charges + reno_monthly
    return {
        "monthly": round(fixed),            # fixed cost, EXCLUDING mortgage
        "breakdown": {"charges": round(charges), "renovation": round(reno_monthly)},
        "financing_fee": round(f["financing_fee"]) if f.get("financing_fee") else None,
        "charge_total": round(f["charge_total"]) if f.get("charge_total") else None,
        "five_year": round(fixed * 60),
        "charges_estimated": charges_est,
    }
