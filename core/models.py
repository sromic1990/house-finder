"""Common data model shared across all sources and criteria.

The `Listing.features` dict is intentionally open-ended: source adapters write
normalized attributes into it (sauna, balcony, parking, energy_class, ...), and
criteria read from it. This is what lets you add new criteria later WITHOUT
changing this model or the adapters that don't know about the new attribute.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ---- Canonical vocabulary for common features -----------------------------
# Adapters normalize source-specific text into these values so criteria can
# rely on a stable vocabulary. Unknown/unavailable data should be left absent
# (None / key missing) rather than guessed.

PARKING_TYPES = [
    "garage",      # locked private garage
    "hall",        # parking hall / covered communal structure
    "covered",     # covered spot (carport)
    "own_spot",    # a dedicated own parking spot (oma autopaikka), unroofed
    "open_pole",   # open spot with an engine heating pole
    "open",        # open spot, no heating pole
    "none",        # no parking
]

# Canonical property types (Finnish portal vocabulary).
PROPERTY_TYPES = [
    "omakotitalo",   # detached house
    "paritalo",      # semi-detached
    "rivitalo",      # terraced / row house
    "erillistalo",   # detached unit in a housing company
    "luhtitalo",     # walk-up / gallery-access block
    "kerrostalo",    # apartment block
]


@dataclass
class Sauna:
    present: bool = False
    private: bool = False          # own sauna inside the apartment
    shared: bool = False           # building/shared sauna only


@dataclass
class Balcony:
    present: bool = False
    glazed: Optional[bool] = None  # None = unknown


@dataclass
class Listing:
    # Identity
    source: str                    # "etuovi" | "oikotie" | ...
    source_id: str                 # stable id within that source
    url: str

    # Core facts
    title: str = ""
    deal_type: str = "sale"        # "sale" | "rent"
    price: Optional[float] = None          # EUR (debt-free price for sales)
    maintenance_fee: Optional[float] = None  # EUR / month
    size_m2: Optional[float] = None
    rooms: Optional[float] = None          # total rooms (incl. living room, excl. kitchen)
    bedrooms: Optional[int] = None         # if unknown, criteria derive rooms-1
    room_desc: str = ""            # e.g. "3h + k + s"
    year_built: Optional[int] = None
    floor: Optional[str] = None    # e.g. "3/5"
    property_type: str = ""        # canonical: omakotitalo|rivitalo|paritalo|kerrostalo|...

    # Location
    address: str = ""
    district: str = ""
    city: str = "Helsinki"
    lat: Optional[float] = None
    lon: Optional[float] = None

    # Media
    photos: list[str] = field(default_factory=list)
    floor_plans: list[str] = field(default_factory=list)

    # Open-ended, criteria-readable attributes (sauna, balcony, parking, ...)
    features: dict[str, Any] = field(default_factory=dict)

    # Bookkeeping (filled by the collector, not the adapters)
    raw: dict[str, Any] = field(default_factory=dict)
    first_seen: Optional[str] = None   # ISO timestamp
    last_seen: Optional[str] = None    # ISO timestamp
    delisted: bool = False
    prev_price: Optional[float] = None       # price just before the most recent drop
    price_dropped_at: Optional[str] = None   # when we detected that drop (ISO)

    # ---- Derived helpers --------------------------------------------------
    @property
    def uid(self) -> str:
        """Globally unique key used for dedup + history tracking."""
        return f"{self.source}:{self.source_id}"

    @property
    def price_per_m2(self) -> Optional[float]:
        if self.price and self.size_m2:
            return round(self.price / self.size_m2, 1)
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
