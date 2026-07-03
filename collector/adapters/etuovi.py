"""Etuovi adapter — live.

Data path (verified): each search-results page embeds window.__INITIAL_STATE__,
a JS object (contains bare `undefined`, so we sanitize -> null and raw_decode
the first object). Listings live at
announcementListV3.searchResults.announcements . We fetch the per-house-type
SEO search paths (omakotitalot / rivitalot / paritalot) for Helsinki, paginate
with ?sivu=N, and map each announcement to a Listing. roomStructure feeds the
Finnish feature parsers.

Personal, low-volume use. Against Etuovi ToS; markup can change.
Ships behind sources.etuovi.enabled.
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

log = logging.getLogger("adapter.etuovi")

BASE = "https://www.etuovi.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "fi-FI,fi;q=0.9,en;q=0.8",
}

# Etuovi filters property type via a path suffix: /myytavat-asunnot/{city}/{type}
DEFAULT_HOUSE_TYPES = ["omakotitalo", "rivitalo", "paritalo"]

PROPERTY_SUBTYPE = {
    "APARTMENT_HOUSE": "kerrostalo",
    "ROW_HOUSE": "rivitalo",
    "DETACHED_HOUSE": "omakotitalo",
    "PAIRED_HOUSE": "paritalo",
    "SEMI_DETACHED_HOUSE": "paritalo",
    "SEPARATE_HOUSE": "erillistalo",
    "BALCONY_ACCESS_HOUSE": "luhtitalo",
}


class EtuoviAdapter(SourceAdapter):
    name = "etuovi"

    def fetch(self, search: dict) -> list[Listing]:
        house_types = self.config.get("house_types", DEFAULT_HOUSE_TYPES)
        city = (search.get("city") or "helsinki").lower()
        max_pages = int(self.config.get("max_pages", 20))
        listings: list[Listing] = []
        for htype in house_types:
            try:
                listings.extend(self._fetch_type(htype, city, max_pages))
            except Exception as exc:
                log.error("etuovi type %s failed: %s", htype, exc)
        log.info("etuovi: %d listings across %d types", len(listings), len(house_types))
        return listings

    def _fetch_type(self, htype, city, max_pages) -> list[Listing]:
        out = []
        for page in range(1, max_pages + 1):
            url = f"{BASE}/myytavat-asunnot/{city}/{htype}" + (f"?sivu={page}" if page > 1 else "")
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            anns = self._extract_announcements(r.text)
            if not anns:
                break
            for a in anns:
                lst = self._to_listing(a)
                if lst:
                    out.append(lst)
            if len(anns) < 30:      # last page
                break
            time.sleep(0.5)
        return out

    @staticmethod
    def _extract_announcements(html: str) -> list[dict]:
        i = html.find("__INITIAL_STATE__")
        if i < 0:
            return []
        blob = html[html.find("=", i) + 1:].lstrip()
        blob = re.sub(r"\bundefined\b", "null", blob)
        try:
            obj, _ = json.JSONDecoder().raw_decode(blob)
        except Exception:
            return []
        return (((obj.get("announcementListV3") or {}).get("searchResults") or {})
                .get("announcements") or [])

    def _to_listing(self, a: dict) -> Listing | None:
        # Skip part-ownership / non-standard products: these have alphabetic
        # friendlyIds and a share/debt figure in searchPrice (often with cents)
        # rather than a debt-free total price, which would distort budgeting.
        fid = a.get("friendlyId")
        if not (fid and str(fid).isdigit()):
            return None

        rs = a.get("roomStructure") or ""
        feats = self._features_from_text(rs)
        if "sauna" in feats and feats["sauna"]["present"] and "taloyht" not in rs.lower():
            feats["sauna"]["private"] = True

        # district + city from "Roihuvuori Helsinki"
        addr2 = (a.get("addressLine2") or "").rsplit(" ", 1)
        district = addr2[0] if len(addr2) == 2 else ""
        city = addr2[1] if len(addr2) == 2 else (a.get("addressLine2") or "Helsinki")

        return Listing(
            source=self.name,
            source_id=str(fid or a.get("id")),
            url=f"{BASE}/kohde/{fid}" if fid else "",
            title=(a.get("addressLine1") or "") + (f", {district}" if district else ""),
            price=a.get("searchPrice"),
            size_m2=a.get("area"),
            rooms=self._rooms(rs, a.get("roomCount")),
            room_desc=rs,
            year_built=a.get("constructionFinishedYear"),
            floor=(f"{a.get('floorLevel')}/{a.get('housingCompanyFloorCount')}"
                   if a.get("floorLevel") is not None else None),
            property_type=PROPERTY_SUBTYPE.get(a.get("propertySubtype"), ""),
            address=a.get("addressLine1", ""),
            district=district,
            city=city,
            lat=a.get("latitude"),
            lon=a.get("longitude"),
            photos=self._image(a.get("mainImageUri"), a.get("mainImageHidden")),
            features=feats,
            raw={"propertyType": a.get("propertyType"),
                 "propertySubtype": a.get("propertySubtype")},
        )

    @staticmethod
    def _rooms(room_structure: str, room_count_enum) -> float | None:
        m = re.match(r"\s*(\d+)", room_structure or "")
        if m:
            return float(m.group(1))
        enum_map = {"ONE_ROOM": 1, "TWO_ROOMS": 2, "THREE_ROOMS": 3,
                    "FOUR_ROOMS": 4, "FIVE_ROOMS": 5, "SIX_ROOMS": 6, "SEVEN_ROOMS": 7}
        return enum_map.get(room_count_enum)

    @staticmethod
    def _image(uri, hidden) -> list[str]:
        if not uri or hidden:
            return []
        full = uri.replace("{imageParameters}", "1024x768")
        if full.startswith("//"):
            full = "https:" + full
        return [full]

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
