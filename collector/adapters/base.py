"""Source adapter interface.

A source adapter's only job: fetch listings for the configured search and return
them as normalized `Listing` objects. Everything downstream (dedup, history,
scoring, UI, email) is source-agnostic, so adding a portal = adding one adapter.

Contract:
  * name: stable source key (also used in Listing.source and config.sources)
  * fetch(search) -> list[Listing]   (raise nothing fatal; log + return [] on failure)

Populate Listing.features with the canonical vocabulary the criteria expect:
  features["sauna"]   = {"present": bool, "private": bool, "shared": bool}
  features["balcony"] = {"present": bool, "glazed": Optional[bool]}
  features["parking"] = {"type": one of core.models.PARKING_TYPES}
Leave a feature absent (don't guess) when the source doesn't state it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models import Listing


class SourceAdapter(ABC):
    name: str = ""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def fetch(self, search: dict) -> list[Listing]:
        """Return normalized listings for the given search config block."""
        ...
