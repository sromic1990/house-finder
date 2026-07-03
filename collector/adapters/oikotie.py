"""Oikotie Asunnot adapter — live.

Data path (verified): the search page embeds three tokens in <meta> tags
(api-token / loaded / cuid) which authenticate the internal JSON cards API at
https://asunnot.oikotie.fi/api/cards . We query it filtered to Helsinki house
building-types with the coarse price/size pre-filters, paginate, and map each
card to a Listing. The card carries price, size, rooms, coordinates, images and
buildingData (year, buildingType, floor count) plus a roomConfiguration string
we run through the Finnish feature parsers.

Personal, low-volume use. Against Oikotie ToS; markup/endpoints can change.
Ships behind sources.oikotie.enabled; no separate 'verified' flag needed now.
"""
from __future__ import annotations

import json
import logging
import re
import time

import requests

from core.models import Listing
from ..normalize import (parse_balcony, parse_duplex, parse_parking,
                         parse_sauna, parse_toilets)
from .base import SourceAdapter

log = logging.getLogger("adapter.oikotie")

BASE = "https://asunnot.oikotie.fi"
SEARCH_PAGE = f"{BASE}/myytavat-asunnot/helsinki"
CARDS_API = f"{BASE}/api/cards"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "fi-FI,fi;q=0.9,en;q=0.8",
}

# Oikotie building-type codes -> our canonical property types.
BUILDING_TYPE = {
    1: "kerrostalo", 2: "rivitalo", 4: "paritalo",
    8: "luhtitalo", 16: "erillistalo", 32: "omakotitalo",
    64: "erillistalo", 128: "kerrostalo", 256: "luhtitalo",
}
# House building-types fetched by default (rivitalo, paritalo, omakotitalo).
DEFAULT_HOUSE_TYPES = [2, 4, 32]

# Oikotie location tuples [id, type=6 (municipality), name]. Add more as needed.
CITY_LOCATIONS = {
    "helsinki": [64, 6, "Helsinki"],
    "espoo": [49, 6, "Espoo"],
    "vantaa": [92, 6, "Vantaa"],
}


class OikotieAdapter(SourceAdapter):
    name = "oikotie"

    def fetch(self, search: dict) -> list[Listing]:
        try:
            tokens = self._bootstrap_tokens()
        except Exception as exc:
            log.error("oikotie token bootstrap failed: %s", exc)
            return []

        cities = search.get("cities") or [search.get("city", "Helsinki")]
        house_types = self.config.get("building_types", DEFAULT_HOUSE_TYPES)
        listings: list[Listing] = []
        for city in cities:
            loc = CITY_LOCATIONS.get(city.lower())
            if not loc:
                log.warning("oikotie: no location code for %r — add it to CITY_LOCATIONS", city)
                continue
            loc_json = json.dumps([loc], separators=(",", ":"))
            for bt in house_types:
                try:
                    listings.extend(self._fetch_type(bt, search, tokens, loc_json))
                except Exception as exc:
                    log.error("oikotie %s buildingType=%s failed: %s", city, bt, exc)
        log.info("oikotie: %d listings across %s x types %s", len(listings), cities, house_types)
        return listings

    # ---- internals -------------------------------------------------------
    def _bootstrap_tokens(self) -> dict:
        r = requests.get(SEARCH_PAGE, headers=HEADERS, timeout=25)
        r.raise_for_status()

        def meta(name):
            m = re.search(rf'<meta name="{name}" content="([^"]+)"', r.text)
            if not m:
                raise RuntimeError(f"token {name!r} not found on search page")
            return m.group(1)

        return {"OTA-token": meta("api-token"), "OTA-loaded": meta("loaded"),
                "OTA-cuid": meta("cuid")}

    def _fetch_type(self, bt, search, tokens, loc_json) -> list[Listing]:
        hdr = {**HEADERS, **tokens, "Accept": "application/json"}
        base_params = {
            "cardType": 100,
            "locations": loc_json,
            "buildingType[]": bt,
            "sortBy": "published_sort_desc",
        }
        if search.get("price_max"):
            base_params["price[max]"] = int(search["price_max"])
        if search.get("size_min_m2"):
            base_params["size[min]"] = int(search["size_min_m2"])

        out, offset, limit = [], 0, 100
        while True:
            params = {**base_params, "limit": limit, "offset": offset}
            r = requests.get(CARDS_API, headers=hdr, params=params, timeout=25)
            r.raise_for_status()
            data = r.json()
            cards = data.get("cards", [])
            for c in cards:
                lst = self._card_to_listing(c)
                if lst:
                    out.append(lst)
            offset += limit
            if offset >= data.get("found", 0) or not cards:
                break
            time.sleep(0.5)  # be gentle
        return out

    def _card_to_listing(self, c: dict) -> Listing | None:
        bd = c.get("buildingData") or {}
        coords = c.get("coordinates") or {}
        images = c.get("images") or {}
        rc = c.get("roomConfiguration") or ""

        feats = self._features_from_text(rc)
        # A sauna listed in the unit's own room configuration is private,
        # unless it's explicitly the housing-company (shared) sauna.
        if "sauna" in feats and feats["sauna"]["present"] and "taloyht" not in rc.lower():
            feats["sauna"]["private"] = True

        floor_count = bd.get("floorCount")
        btype = bd.get("buildingType")
        # For houses, a 2+ storey building means a two-floor (duplex) home.
        if floor_count and btype in (2, 4, 16, 32, 64):
            feats["duplex"] = floor_count >= 2

        addr = bd.get("address", "")
        district = bd.get("district", "")
        title = f"{addr}, {district}" if addr and district else (
            addr or rc or c.get("description") or "").strip()[:120]

        return Listing(
            source=self.name,
            source_id=str(c.get("id")),
            url=c.get("url", ""),
            title=title,
            price=self._parse_price(c.get("price")),
            size_m2=c.get("size"),
            rooms=c.get("rooms"),
            room_desc=rc,
            year_built=bd.get("year"),
            floor=f"{bd.get('floor')}/{floor_count}" if floor_count else None,
            property_type=BUILDING_TYPE.get(btype, ""),
            address=bd.get("address", ""),
            district=bd.get("district", ""),
            city=bd.get("city", "Helsinki"),
            lat=coords.get("latitude"),
            lon=coords.get("longitude"),
            photos=[images["wide"]] if images.get("wide") else [],
            features=feats,
            raw={"cardSubType": c.get("cardSubType")},
        )

    @staticmethod
    def _parse_price(price) -> float | None:
        if not price:
            return None
        digits = re.sub(r"[^\d]", "", str(price))
        return float(digits) if digits else None

    @staticmethod
    def _features_from_text(*texts: str) -> dict:
        blob = " ".join(t for t in texts if t)
        feats = {}
        if (s := parse_sauna(blob)) is not None:
            feats["sauna"] = s
        if (b := parse_balcony(blob)) is not None:
            feats["balcony"] = b
        if (p := parse_parking(blob)) is not None:
            feats["parking"] = p
        if (n := parse_toilets(blob)) is not None:
            feats["toilets"] = n
        if (d := parse_duplex(blob)) is not None:
            feats["duplex"] = d
        return feats
