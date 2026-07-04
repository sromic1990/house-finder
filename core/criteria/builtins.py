"""Built-in criteria.

Each class is small and self-contained — copy one as a template to add your own.
Hard requirements are FilterCriterion; ranking preferences are ScoreCriterion.
All thresholds/weights come from config.yaml, never hard-coded here.
"""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Optional

from .base import Contribution, FilterCriterion, ScoreCriterion, Verdict
from .registry import register


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(h))


# ===========================================================================
# HARD REQUIREMENTS (filters)
# ===========================================================================
@register
class SaunaRequired(FilterCriterion):
    """Exclude listings without a private sauna.

    config:
      require_private: true      # own sauna, not just a shared building sauna
      on_unknown: keep|exclude   # what to do when sauna data is missing
    """
    key = "sauna"
    title = "Private sauna"

    def check(self, listing) -> Verdict:
        s = listing.features.get("sauna")
        require_private = self.config.get("require_private", True)
        on_unknown = self.config.get("on_unknown", "keep")

        if s is None:
            if on_unknown == "exclude":
                return Verdict(False, "sauna unknown")
            return Verdict(True, "sauna unknown (kept)")

        present = s.get("present", False)
        private = s.get("private", False)
        if not present:
            return Verdict(False, "no sauna")
        if require_private and not private:
            return Verdict(False, "shared sauna only")
        return Verdict(True, "private sauna")


@register
class GlazedBalcony(FilterCriterion):
    """If the listing has a balcony, it must be glazed. No balcony = fine.

    config:
      on_unknown_glazing: keep|exclude   # balcony present but glazing unknown
    """
    key = "balcony_glazed"
    title = "Glazed balcony (if any)"

    def check(self, listing) -> Verdict:
        b = listing.features.get("balcony")
        if b is None or not b.get("present", False):
            return Verdict(True, "no balcony")
        glazed = b.get("glazed")
        if glazed is True:
            return Verdict(True, "glazed balcony")
        if glazed is False:
            return Verdict(False, "balcony not glazed")
        # unknown glazing
        if self.config.get("on_unknown_glazing", "keep") == "exclude":
            return Verdict(False, "balcony glazing unknown")
        return Verdict(True, "balcony glazing unknown (kept)")


@register
class BudgetCap(FilterCriterion):
    """Exclude listings above the maximum budget.

    config:
      max_price: 400000
      on_unknown: keep|exclude
    """
    key = "budget"
    title = "Budget"

    def check(self, listing) -> Verdict:
        cap = self.config.get("max_price")
        if listing.price is None:
            keep = self.config.get("on_unknown", "keep") != "exclude"
            return Verdict(keep, "price unknown")
        if cap and float(listing.price) > float(cap):
            return Verdict(False, f"€{listing.price:,.0f} over €{float(cap):,.0f}")
        return Verdict(True, f"within €{float(cap):,.0f}" if cap else "no cap")


@register
class MinSize(FilterCriterion):
    """Exclude listings smaller than the minimum living area.

    config:
      min_m2: 100
      on_unknown: keep|exclude
    """
    key = "min_size"
    title = "Minimum size"

    def check(self, listing) -> Verdict:
        lo = self.config.get("min_m2")
        if listing.size_m2 is None:
            keep = self.config.get("on_unknown", "keep") != "exclude"
            return Verdict(keep, "size unknown")
        if lo and float(listing.size_m2) < float(lo):
            return Verdict(False, f"{listing.size_m2:g} m² under {float(lo):g} m²")
        return Verdict(True, f"≥ {float(lo):g} m²" if lo else "no minimum")


def _keep_on_unknown(cfg, reason) -> Verdict:
    return Verdict(cfg.get("on_unknown", "keep") != "exclude", reason)


@register
class OwnershipSanity(FilterCriterion):
    """Exclude non-purchase listings (asumisoikeus / osaomistus / part-ownership).

    Their listed price is a share/occupancy fee, giving an implausibly low
    €/m² that no genuine full-ownership Helsinki home reaches. A price-per-m²
    floor is a robust, source-agnostic way to drop them.
    config:  min_price_per_m2: 1500
    """
    key = "ownership_sanity"
    title = "Full ownership"

    def check(self, listing) -> Verdict:
        ppm2 = listing.price_per_m2
        floor = float(self.config.get("min_price_per_m2", 1500))
        if ppm2 is None:
            return _keep_on_unknown(self.config, "price/size unknown")
        if ppm2 < floor:
            return Verdict(False, f"€{ppm2:,.0f}/m² — part-ownership/asumisoikeus")
        return Verdict(True, "full ownership")


@register
class ResidentialCompleteness(FilterCriterion):
    """Drop entries that aren't a concrete residential home for sale.

    A real apartment/house listing always states a living area and a price.
    Plots, commercial/office premises and 'coming soon' stubs usually omit the
    living area, so they sail past the size/budget filters that KEEP unknowns —
    this catches them. As a second layer it also drops anything whose title
    names it as business premises (a home title never says "toimisto").
    config:
      require: [size, price]                  # any listed field missing -> excluded
      exclude_keywords: [toimisto, ...]        # commercial words in the title -> excluded
    """
    key = "listing_complete"
    title = "Complete home listing"
    _CHECKS = {
        "size": (lambda l: l.size_m2 is not None, "no living area stated — not a residential home"),
        "price": (lambda l: l.price is not None, "no sale price stated"),
        "rooms": (lambda l: l.rooms is not None, "no room count stated"),
    }
    # Unambiguous business-premises terms — none appear in a residential title.
    _COMMERCIAL = ("toimisto", "liikehuoneist", "liikekiinteist", "toimitila",
                   "liiketila", "myymälä", "teollisuus")

    def check(self, listing) -> Verdict:
        for field in self.config.get("require", ["size", "price"]):
            chk = self._CHECKS.get(field)
            if chk and not chk[0](listing):
                return Verdict(False, chk[1])
        blob = (listing.title or "").lower()
        for w in self.config.get("exclude_keywords", self._COMMERCIAL):
            if w in blob:
                return Verdict(False, f"business premises, not a home ({w})")
        return Verdict(True, "complete listing")


@register
class PropertyTypeAllowed(FilterCriterion):
    """Only allow certain property types (omakotitalo / rivitalo / ...).

    config:  allowed: [omakotitalo, rivitalo, paritalo]
    """
    key = "property_type"
    title = "Property type"

    def check(self, listing) -> Verdict:
        allowed = [a.lower() for a in self.config.get("allowed", [])]
        pt = (listing.property_type or "").lower()
        if not pt:
            return _keep_on_unknown(self.config, "type unknown")
        if allowed and pt not in allowed:
            return Verdict(False, f"{pt} not in allowed types")
        return Verdict(True, pt)


@register
class BedroomsRange(FilterCriterion):
    """Bedrooms (excluding the living room). Derives rooms-1 if not explicit.

    config:  min: 3   max: 5
    """
    key = "bedrooms"
    title = "Bedrooms"

    def _bedrooms(self, listing):
        if listing.bedrooms is not None:
            return int(listing.bedrooms)
        if listing.rooms is not None:      # Finnish 'Nh' counts living room
            return int(listing.rooms) - 1
        return None

    def check(self, listing) -> Verdict:
        b = self._bedrooms(listing)
        lo, hi = self.config.get("min"), self.config.get("max")
        if b is None:
            return _keep_on_unknown(self.config, "bedrooms unknown")
        if lo is not None and b < lo:
            return Verdict(False, f"{b} bedrooms under {lo}")
        if hi is not None and b > hi:
            return Verdict(False, f"{b} bedrooms over {hi}")
        return Verdict(True, f"{b} bedrooms")


@register
class DuplexRequired(FilterCriterion):
    """Require a two-floor (maisonette) home. config: required: true"""
    key = "duplex"
    title = "Duplex"

    def check(self, listing) -> Verdict:
        d = listing.features.get("duplex")
        if d is None:
            return _keep_on_unknown(self.config, "duplex unknown")
        return Verdict(bool(d), "duplex" if d else "single floor")


@register
class MinToilets(FilterCriterion):
    """Require at least N toilets. config: min: 2"""
    key = "toilets"
    title = "Toilets"

    def check(self, listing) -> Verdict:
        n = listing.features.get("toilets")
        need = int(self.config.get("min", 2))
        if n is None:
            return _keep_on_unknown(self.config, "toilets unknown")
        if n < need:
            return Verdict(False, f"{n} toilet(s) under {need}")
        return Verdict(True, f"{n} toilets")


@register
class ParkingMinimum(FilterCriterion):
    """Exclude unacceptable parking. The deal-breaker is an open spot with a
    heating pole (open_pole) or no parking; garage/hall/covered/own spot are ok.
    config: allowed: [garage, hall, covered, open]
    """
    key = "parking_min"
    title = "Parking"
    _BAD = {"open_pole": "open spot with heating pole", "none": "no parking"}

    def check(self, listing) -> Verdict:
        p = listing.features.get("parking")
        allowed = self.config.get("allowed", ["garage", "hall", "covered", "open"])
        if p is None:
            return _keep_on_unknown(self.config, "parking unknown")
        pt = p.get("type")
        if pt in allowed:
            return Verdict(True, f"parking: {pt}")
        return Verdict(False, self._BAD.get(pt, f"parking: {pt}"))


@register
class BuiltAfter(FilterCriterion):
    """Require year_built >= a minimum. config: year: 1975"""
    key = "built_after"
    title = "Build year"

    def check(self, listing) -> Verdict:
        y = listing.year_built
        minyr = self.config.get("year")
        if y is None:
            return _keep_on_unknown(self.config, "year unknown")
        if minyr and int(y) < int(minyr):
            return Verdict(False, f"built {y}, before {minyr}")
        return Verdict(True, f"built {y}")


@register
class StationWithin(FilterCriterion):
    """Require the nearest metro/train station within max_km. config: max_km: 1.0"""
    key = "station_distance"
    title = "Near a station"

    def check(self, listing) -> Verdict:
        if listing.lat is None or listing.lon is None:
            return _keep_on_unknown(self.config, "location unknown")
        stations = self.config.get("stations") or TransitProximity.DEFAULT_STATIONS
        max_km = float(self.config.get("max_km", 1.0))
        best = min(
            haversine_km(listing.lat, listing.lon,
                         s["lat"] if isinstance(s, dict) else s[1],
                         s["lon"] if isinstance(s, dict) else s[2])
            for s in stations
        )
        txt = f"{best*1000:.0f} m" if best < 1 else f"{best:.1f} km"
        if best <= max_km:
            return Verdict(True, f"{txt} to station")
        return Verdict(False, f"{txt} to nearest station, over {max_km} km")


# ===========================================================================
# RANKING PREFERENCES (scorers)  — all return raw in 0..1
# ===========================================================================
@register
class ParkingPreference(ScoreCriterion):
    """Prefer covered/enclosed parking over open spots.

    config:
      weight: 3
      scores:                    # parking type -> 0..1 desirability
        garage: 1.0
        hall: 0.9
        covered: 0.7
        open_pole: 0.4
        open: 0.15
        none: 0.0
    """
    key = "parking"
    title = "Parking"
    DEFAULT_SCORES = {
        "garage": 1.0, "hall": 0.9, "covered": 0.7, "own_spot": 0.55,
        "open_pole": 0.4, "open": 0.15, "none": 0.0,
    }

    def applies(self, listing) -> bool:
        return listing.features.get("parking") is not None

    LABELS = {"garage": "garage", "hall": "parking hall", "covered": "carport",
              "own_spot": "own spot", "open_pole": "open + heating pole",
              "open": "open spot", "none": "no parking"}

    def score(self, listing) -> Contribution:
        p = listing.features.get("parking") or {}
        ptype = p.get("type")
        table = {**self.DEFAULT_SCORES, **self.config.get("scores", {})}
        if ptype not in table:
            return Contribution(0.0, self.weight, "parking: unknown", applicable=False)
        return Contribution(table[ptype], self.weight,
                            f"parking: {self.LABELS.get(ptype, ptype)}")


@register
class PricePreference(ScoreCriterion):
    """Cheaper ranks higher, measured against a budget ceiling.

    config:
      weight: 4
      max_budget: 550000     # price at/above which score -> 0
      ideal_price: 350000    # price at/below which score -> 1
    """
    key = "price"
    title = "Price"

    def applies(self, listing) -> bool:
        return listing.price is not None

    def score(self, listing) -> Contribution:
        ideal = float(self.config.get("ideal_price", 0))
        ceil = float(self.config.get("max_budget", 0)) or (ideal * 2 or 1)
        p = float(listing.price)
        if p <= ideal:
            raw = 1.0
        elif p >= ceil:
            raw = 0.0
        else:
            raw = (ceil - p) / (ceil - ideal)
        return Contribution(raw, self.weight, f"price: €{p:,.0f}")


@register
class PricePerM2Preference(ScoreCriterion):
    """Lower €/m² ranks higher (value for money).

    config:
      weight: 2
      good: 6000       # €/m² at/below which score -> 1
      bad: 12000       # €/m² at/above which score -> 0
    """
    key = "price_per_m2"
    title = "Price / m²"

    def applies(self, listing) -> bool:
        return listing.price_per_m2 is not None

    def score(self, listing) -> Contribution:
        good = float(self.config.get("good", 6000))
        bad = float(self.config.get("bad", 12000))
        v = listing.price_per_m2
        raw = 1.0 if v <= good else 0.0 if v >= bad else (bad - v) / (bad - good)
        return Contribution(raw, self.weight, f"€/m²: {v:,.0f}")


@register
class SizePreference(ScoreCriterion):
    """Bigger is better up to a target; below a minimum scores 0.

    config:
      weight: 2
      min_m2: 45        # below this -> 0
      target_m2: 90     # at/above this -> 1
    """
    key = "size"
    title = "Size"

    def applies(self, listing) -> bool:
        return listing.size_m2 is not None

    def score(self, listing) -> Contribution:
        lo = float(self.config.get("min_m2", 0))
        hi = float(self.config.get("target_m2", lo + 50))
        a = float(listing.size_m2)
        raw = 0.0 if a <= lo else 1.0 if a >= hi else (a - lo) / (hi - lo)
        return Contribution(raw, self.weight, f"size: {a:g} m²")


@register
class RoomsPreference(ScoreCriterion):
    """More rooms better up to a target.

    config:
      weight: 1
      min_rooms: 2
      target_rooms: 4
    """
    key = "rooms"
    title = "Rooms"

    def applies(self, listing) -> bool:
        return listing.rooms is not None

    def score(self, listing) -> Contribution:
        lo = float(self.config.get("min_rooms", 1))
        hi = float(self.config.get("target_rooms", lo + 2))
        r = float(listing.rooms)
        raw = 0.0 if r <= lo else 1.0 if r >= hi else (r - lo) / (hi - lo)
        return Contribution(raw, self.weight, f"rooms: {r:g}")


@register
class CommutePreference(ScoreCriterion):
    """Closer to any configured target (straight-line km) ranks higher.

    config:
      weight: 3
      targets:
        - {name: "Office", lat: 60.1699, lon: 24.9384}
      near_km: 1.5     # at/under this distance -> 1
      far_km: 12       # at/over this distance -> 0
    """
    key = "commute"
    title = "Location / commute"

    def applies(self, listing) -> bool:
        return (listing.lat is not None and listing.lon is not None
                and bool(self.config.get("targets")))

    @staticmethod
    def _haversine_km(lat1, lon1, lat2, lon2) -> float:
        r = 6371.0
        dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
        h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return 2 * r * asin(sqrt(h))

    def score(self, listing) -> Contribution:
        near = float(self.config.get("near_km", 1.5))
        far = float(self.config.get("far_km", 12))
        dists = [
            self._haversine_km(listing.lat, listing.lon, t["lat"], t["lon"])
            for t in self.config["targets"]
        ]
        d = min(dists)
        raw = 1.0 if d <= near else 0.0 if d >= far else (far - d) / (far - near)
        return Contribution(raw, self.weight, f"{d:.1f} km to nearest target")


@register
class TransitProximity(ScoreCriterion):
    """Prefer listings close to a metro or commuter-train station.

    config:
      weight: 4
      near_km: 0.6      # at/under this -> 1 (a short walk)
      far_km: 2.5       # at/over this  -> 0
      stations: []      # optional override; defaults to Helsinki metro + rail
    """
    key = "transit"
    title = "Public transport"

    # Helsinki metro (M1/M2) + major commuter-rail stations. Override in config.
    DEFAULT_STATIONS = [
        ("Ruoholahti", 60.1631, 24.9153), ("Kamppi", 60.1690, 24.9320),
        ("Rautatientori", 60.1710, 24.9430), ("Kaisaniemi", 60.1725, 24.9490),
        ("Hakaniemi", 60.1790, 24.9490), ("Sörnäinen", 60.1875, 24.9600),
        ("Kalasatama", 60.1876, 24.9787), ("Kulosaari", 60.1880, 24.9990),
        ("Herttoniemi", 60.1950, 25.0300), ("Siilitie", 60.2060, 25.0430),
        ("Itäkeskus", 60.2100, 25.0820), ("Myllypuro", 60.2240, 25.0770),
        ("Kontula", 60.2330, 25.0900), ("Mellunmäki", 60.2410, 25.1080),
        ("Puotila", 60.2160, 25.0930), ("Rastila", 60.2020, 25.1200),
        ("Vuosaari", 60.2070, 25.1440),
        ("Pasila", 60.1985, 24.9330), ("Ilmala", 60.2100, 24.9200),
        ("Käpylä", 60.2200, 24.9530), ("Oulunkylä", 60.2290, 24.9680),
        ("Malmi", 60.2510, 25.0110), ("Tikkurila", 60.2920, 25.0440),
        ("Huopalahti", 60.2180, 24.8930), ("Kannelmäki", 60.2430, 24.8830),
        ("Pohjois-Haaga", 60.2280, 24.8930), ("Leppävaara", 60.2190, 24.8130),
        # Espoo — West Metro
        ("Koivusaari", 60.1583, 24.8869), ("Keilaniemi", 60.1755, 24.8285),
        ("Aalto-yliopisto", 60.1846, 24.8255), ("Tapiola", 60.1759, 24.8046),
        ("Urheilupuisto", 60.1720, 24.7906), ("Niittykumpu", 60.1698, 24.7770),
        ("Matinkylä", 60.1600, 24.7385), ("Finnoo", 60.1520, 24.7290),
        ("Kaitaa", 60.1530, 24.7050), ("Soukka", 60.1460, 24.6870),
        ("Espoonlahti", 60.1490, 24.6600), ("Kivenlahti", 60.1530, 24.6470),
        # Espoo — commuter rail (Rantarata)
        ("Kilo", 60.2160, 24.8010), ("Kera", 60.2185, 24.7760),
        ("Kauniainen", 60.2115, 24.7290), ("Koivuhovi", 60.2185, 24.7040),
        ("Tuomarila", 60.2230, 24.6820), ("Espoon keskus", 60.2255, 24.6560),
        ("Kauklahti", 60.2010, 24.6060),
        # Vantaa — Ring Rail + main line
        ("Hiekkaharju", 60.3010, 25.0430), ("Rekola", 60.3130, 25.0760),
        ("Koivukylä", 60.3230, 25.0630), ("Aviapolis", 60.2930, 24.9560),
        ("Lentoasema", 60.3120, 24.9670), ("Leinelä", 60.2990, 25.0270),
        ("Kivistö", 60.3120, 24.8460), ("Vantaankoski", 60.2920, 24.8420),
        ("Myyrmäki", 60.2610, 24.8540), ("Louhela", 60.2680, 24.8480),
        ("Martinlaakso", 60.2760, 24.8470),
    ]

    def applies(self, listing) -> bool:
        return listing.lat is not None and listing.lon is not None

    def score(self, listing) -> Contribution:
        near = float(self.config.get("near_km", 0.6))
        far = float(self.config.get("far_km", 2.5))
        stations = self.config.get("stations") or self.DEFAULT_STATIONS
        best_name, best_d = None, float("inf")
        for st in stations:
            name, lat, lon = (st["name"], st["lat"], st["lon"]) if isinstance(st, dict) \
                else (st[0], st[1], st[2])
            dkm = haversine_km(listing.lat, listing.lon, lat, lon)
            if dkm < best_d:
                best_name, best_d = name, dkm
        raw = 1.0 if best_d <= near else 0.0 if best_d >= far else (far - best_d) / (far - near)
        dist_txt = f"{best_d*1000:.0f} m" if best_d < 1 else f"{best_d:.1f} km"
        return Contribution(raw, self.weight, f"{dist_txt} to {best_name} station")


@register
class OwnLandPreference(ScoreCriterion):
    """Prioritize houses on owned land (oma tontti) over leased plots.

    Flagged `priority` in config so it acts as a ranking TIER: every own-land
    house ranks above every leased-plot one, and the normal score orders within
    each tier. Land ownership is read from the detail page for survivors
    (collector/enrich.py); until known it doesn't apply (no penalty).
    config:  priority: true   scores: {own: 1.0, optional_rental: 0.4, rented: 0.0}
    """
    key = "own_land"
    title = "Land ownership"
    DEFAULT_SCORES = {"own": 1.0, "optional_rental": 0.4, "rented": 0.0}
    _LABEL = {"own": "own land", "rented": "rented plot",
              "optional_rental": "optional rental plot"}

    def applies(self, listing) -> bool:
        return listing.features.get("land_ownership") is not None

    def score(self, listing) -> Contribution:
        v = listing.features.get("land_ownership")
        table = {**self.DEFAULT_SCORES, **self.config.get("scores", {})}
        return Contribution(table.get(v, 0.0), self.weight, self._LABEL.get(v, v))


@register
class TravelTimeScore(ScoreCriterion):
    """Prefer shorter public-transport travel time to center/airport (not a hard cut).

    Reads listing.features['transit_minutes'] = {"center": int, "airport": int},
    populated by the collector's routing step (core/routing.py, needs a key).
    When routing data is absent this criterion simply doesn't apply (no penalty).
    config:  weight: 3   good_minutes: 30   bad_minutes: 60   destinations: [center, airport]
    """
    key = "travel_time"
    title = "Transit time"

    def _minutes(self, listing):
        tt = listing.features.get("transit_minutes") or {}
        dests = self.config.get("destinations", ["center", "airport"])
        vals = [tt[d] for d in dests if tt.get(d) is not None]
        return min(vals) if vals else None

    def applies(self, listing) -> bool:
        return self._minutes(listing) is not None

    def score(self, listing) -> Contribution:
        best = self._minutes(listing)
        good = float(self.config.get("good_minutes", 30))
        bad = float(self.config.get("bad_minutes", 60))
        raw = 1.0 if best <= good else 0.0 if best >= bad else (bad - best) / (bad - good)
        return Contribution(raw, self.weight, f"{best:.0f} min by transit")
