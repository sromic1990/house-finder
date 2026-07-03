"""Fixed monthly cost to own — EXCLUDING your personal home loan.

Your mortgage depends on your down payment, rate and term (your decisions), so
we don't guess it. Instead we surface the fixed monthly costs you can't avoid,
so you can size a mortgage that keeps your total within budget:

  maintenance — the monthly upkeep charge (hoitovastike) scraped from the
                listing. For a detached house (no taloyhtiö) it's a per-m²
                running-cost estimate (heating, water, insurance, property tax,
                upkeep), flagged as estimated.
  financing   — the taloyhtiö loan payment already attached to the flat
                (pääomavastike / rahoitusvastike), scraped from the listing.
                Together with maintenance this is the "Yhtiövastike yhteensä"
                the portal shows, so our total is never below the site's figure.
                (If you instead buy the flat debt-free / velaton, this amount
                folds into YOUR mortgage rather than a monthly vastike.)
  renovation  — an ESTIMATE of money to set aside monthly for big renovations
                over the next 5 years, weighted by the (renovation-aware) risk
                score. This is the "surprises" line — the only estimated part
                when the charges are scraped.

Excluded on purpose: your own home loan (mortgage) — you size that against this.
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
    fin = f.get("financing_fee") or 0.0
    total_stated = f.get("charge_total")

    if maint is not None:
        # Full monthly vastike = maintenance + any company-loan share (pääomavastike).
        maintenance, financing, charges_est = maint, fin, False
    elif total_stated:
        # Only the combined "Yhtiövastike yhteensä" is stated — use it as one line.
        maintenance, financing, charges_est = total_stated, 0.0, False
    elif listing.property_type in DETACHED:
        maintenance, financing, charges_est = size * c["detached_running_per_m2"], 0.0, True
    else:
        maintenance, financing, charges_est = size * c["est_charge_per_m2"], 0.0, True

    charges = maintenance + financing
    reno_monthly = (reno_score * c["reno_per_m2"] * size) / 60.0
    fixed = charges + reno_monthly
    return {
        "monthly": round(fixed),            # fixed cost, EXCLUDING your mortgage
        "breakdown": {
            "maintenance": round(maintenance),
            "financing": round(financing),
            "renovation": round(reno_monthly),
        },
        "charge_total": round(f["charge_total"]) if f.get("charge_total") else None,
        "five_year": round(fixed * 60),
        "charges_estimated": charges_est,
    }
