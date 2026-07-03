"""Mock source — lets the whole app run end-to-end with no scraping.

Reads listings from collector/adapters/mock_data.json. Great for developing the
UI, scoring, history and email flow before the live scrapers are dialed in.
Enable/disable via config.sources.mock.enabled.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.models import Listing
from .base import SourceAdapter

_DATA = Path(__file__).with_name("mock_data.json")


class MockAdapter(SourceAdapter):
    name = "mock"

    def fetch(self, search: dict) -> list[Listing]:
        if not _DATA.exists():
            return []
        raw = json.loads(_DATA.read_text(encoding="utf-8"))
        out = []
        for d in raw:
            d.setdefault("source", self.name)
            out.append(Listing(**d))
        return out
