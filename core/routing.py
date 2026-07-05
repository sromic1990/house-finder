"""Public-transport travel time via Digitransit (HSL journey planner).

Used to populate listing.features['transit_minutes'] = {"center": m, "airport": m}
so the `travel_time` criterion can enforce "< 30 min by public transport".

Digitransit needs a FREE subscription key (register at
https://portal-api.digitransit.fi/). Put it in env DIGITRANSIT_KEY. Without a
key this module is inert (returns {}), and the travel_time filter falls back to
its on_unknown policy (keep by default) so nothing breaks.

Distances/durations are real routed transit itineraries, not straight lines.
"""
from __future__ import annotations

import os
from typing import Optional

import requests

# HSL GTFS routing v2 endpoint.
ENDPOINT = "https://api.digitransit.fi/routing/v2/hsl/gtfs/v1"

# Default destinations for the "within 30 min" test.
DEFAULT_DESTINATIONS = {
    "center": (60.1719, 24.9414),   # Helsinki Central / Rautatientori
    "airport": (60.3172, 24.9633),  # Helsinki-Vantaa (HEL)
}

_QUERY = """
query Plan($fromLat: Float!, $fromLon: Float!, $toLat: Float!, $toLon: Float!) {
  plan(from: {lat: $fromLat, lon: $fromLon},
       to:   {lat: $toLat,   lon: $toLon},
       numItineraries: 3,
       transportModes: [{mode: TRANSIT}, {mode: WALK}]) {
    itineraries { duration }
  }
}
"""


def _api_key() -> Optional[str]:
    return os.getenv("DIGITRANSIT_KEY")


def _minutes(from_lat, from_lon, to_lat, to_lon, key, timeout=20) -> Optional[float]:
    resp = requests.post(
        ENDPOINT,
        headers={"Content-Type": "application/json", "digitransit-subscription-key": key},
        json={"query": _QUERY, "variables": {
            "fromLat": from_lat, "fromLon": from_lon,
            "toLat": to_lat, "toLon": to_lon}},
        timeout=timeout,
    )
    resp.raise_for_status()
    itineraries = (resp.json().get("data", {}).get("plan", {}) or {}).get("itineraries", [])
    durations = [it["duration"] for it in itineraries if it.get("duration")]
    return round(min(durations) / 60.0) if durations else None


def transit_minutes(lat: float, lon: float, destinations: dict | None = None) -> dict:
    """Return {dest_name: minutes} for each destination, or {} if unavailable."""
    key = _api_key()
    if key is None or lat is None or lon is None:
        return {}
    dests = destinations or DEFAULT_DESTINATIONS
    out = {}
    for name, (dlat, dlon) in dests.items():
        try:
            m = _minutes(lat, lon, dlat, dlon, key)
            if m is not None:
                out[name] = m
        except Exception:
            pass  # best-effort: a failed leg just stays unknown
    return out


def geocode(text: str, key: str | None = None) -> Optional[tuple]:
    """Resolve a free-text address to (lat, lon) via Digitransit geocoding, or None."""
    key = key or _api_key()
    if not key or not text:
        return None
    try:
        r = requests.get(
            "https://api.digitransit.fi/geocoding/v1/search",
            params={"text": text, "size": 1},
            headers={"digitransit-subscription-key": key}, timeout=20)
        r.raise_for_status()
        feats = r.json().get("features") or []
        if feats:
            lon, lat = feats[0]["geometry"]["coordinates"]
            return (float(lat), float(lon))
    except Exception:
        pass
    return None


def configured_destinations(dest_list: list | None) -> dict:
    """Turn config `commute.destinations` (name + lat/lon OR address) into
    {name: (lat, lon)}. Falls back to the city-centre + airport defaults."""
    out = {}
    for d in dest_list or []:
        name = (d or {}).get("name")
        if not name:
            continue
        if d.get("lat") is not None and d.get("lon") is not None:
            out[name] = (float(d["lat"]), float(d["lon"]))
        elif d.get("address"):
            c = geocode(d["address"])
            if c:
                out[name] = c
    return out or dict(DEFAULT_DESTINATIONS)
