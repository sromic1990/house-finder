"""Detail-page enrichment for survivor listings.

Some fields aren't in the list/card endpoints — only on each listing's detail
page: land ownership ("Tontin omistus") and parking. Called only for listings
that survive the list-data filters, so volume stays low.

Parking is read ONLY from a scoped field, never the whole page: browse menus
mention "autotalli" on every page, so a naive scan would mark everything a
garage. Oikotie exposes a clean "Pysäköintitilan kuvaus" field; Etuovi has no
structured parking in its server HTML (it's client-loaded prose), so for Etuovi
we trust only the room code parsed at list time — an unconfirmed garage stays
unknown rather than risk a false positive on an absolute requirement.
"""
from __future__ import annotations

import logging
import re

import requests

from .normalize import parse_land_ownership, parse_parking

log = logging.getLogger("enrich")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "fi-FI,fi;q=0.9,en;q=0.8",
}

_LAND = re.compile(r"Tontin omistus[\s:]*([A-Za-zÄÖÅäöå]+)")
_OIK_PARKING = re.compile(r"Pysäköintitilan kuvaus\s*</dt>\s*<dd[^>]*>(.*?)</dd>", re.S)
# Scope to the UNIT's own parking sentence — the field also describes the housing
# company's and even neighbours' parking (e.g. "Naapuriyhtiöissä on parkkihalli"),
# which must NOT be credited to this listing.
_OIK_UNIT = re.compile(
    r"huoneiston autopaik\w*[:\s]*(.*?)(?:lisätietoja taloyhti|taloyhti[öo]n autopaik|"
    r"naapuriyhti|$)", re.I | re.S)

# "dedicated own spot" phrases — safe on both sources (not a browse-menu term).
_OWN = re.compile(r"kuuluu\s+(?:oma\s+)?autopaikka|asunnolle kuuluu[^.]{0,40}autopaikka|"
                  r"oma autopaikka|oma pysäköintipaikka", re.I)
_POLE = re.compile(r"lämpötolp|lampotolp|sähkötolp|sahkotolp", re.I)

PARK_RANK = {"garage": 5, "hall": 4, "covered": 3, "own_spot": 2.5,
             "open_pole": 2, "open": 1, "none": 0}


def _own_spot(text: str) -> dict | None:
    """Detect a dedicated own spot from unit-attributed prose (safe on Etuovi).

    Downgrades to open_pole if a heating pole is mentioned right next to it, so
    an own spot *with* a pole stays the excluded case.
    """
    m = _OWN.search(text)
    if not m:
        return None
    window = text[max(0, m.start() - 60): m.start() + 80]
    return {"type": "open_pole"} if _POLE.search(window) else {"type": "own_spot"}


def better_parking(a: dict | None, b: dict | None) -> dict | None:
    """Return the more-covered of two parking dicts."""
    if not a:
        return b
    if not b:
        return a
    return a if PARK_RANK.get(a.get("type"), -1) >= PARK_RANK.get(b.get("type"), -1) else b


def fetch_detail(url: str) -> dict:
    """Return detail-only features: {land_ownership?, parking?}."""
    out: dict = {}
    if not url:
        return out
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as exc:
        log.debug("detail fetch failed for %s: %s", url, exc)
        return out
    raw = r.text
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", " ", raw, flags=re.S | re.I))

    m = _LAND.search(text)
    if m and (lo := parse_land_ownership(m.group(1))):
        out["land_ownership"] = lo

    # Parking: SCOPED to the unit's own parking sentence only (never the whole
    # field, which also lists company/neighbour parking).
    parking = None
    if "oikotie" in url:
        pm = _OIK_PARKING.search(raw)
        if pm:
            value = re.sub(r"<[^>]+>", " ", pm.group(1))
            um = _OIK_UNIT.search(value)
            if um:
                parking = parse_parking(um.group(1))
    # "own dedicated spot" prose — safe on both sources (Etuovi has no field).
    parking = better_parking(parking, _own_spot(text))
    if parking:
        out["parking"] = parking

    # Monthly charges + planned/done renovations (both sources render these).
    flat = re.sub(r"\s+", " ", text)

    def _fee(*labels):
        for lab in labels:
            m = re.search(lab + r"\s*([\d.,  ]+?)\s*€", flat)
            if m and (v := _money(m.group(1))):
                return v
        return None

    def _section(label):
        m = re.search(re.escape(label) + r"\s+(.{5,220})", flat)
        if not m:
            return None
        seg = re.split(r"(Tehdyt remontit|Tulevat remontit|Lisätietoa|Energialuokka|"
                       r"Muut maksut|Rakennus|Isännöi|Kohde on)", m.group(1))[0]
        return seg.strip()[:200] or None

    for key, val in (("maintenance", _fee("Hoitovastike")),
                     ("financing_fee", _fee("Pääomavastike", "Rahoitusvastike")),
                     ("charge_total", _fee("Yhtiövastike yhteensä")),
                     ("reno_planned", _section("Tulevat remontit")),
                     ("reno_done", _section("Tehdyt remontit"))):
        if val is not None:
            out[key] = val
    return out


def _money(s: str):
    v = re.sub(r"[^\d,]", "", s or "")   # Finnish: space thousands, comma decimal
    try:
        return float(v.replace(",", ".")) if v else None
    except ValueError:
        return None
