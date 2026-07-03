"""Adapter registry: maps a source key -> adapter class.

Add a new portal by importing its adapter and adding it here (and a
config.sources block). Nothing else changes.
"""
from __future__ import annotations

from .base import SourceAdapter
from .etuovi import EtuoviAdapter
from .mock import MockAdapter
from .oikotie import OikotieAdapter

ADAPTERS = {
    cls.name: cls for cls in (MockAdapter, EtuoviAdapter, OikotieAdapter)
}


def build_adapters(sources_config: dict) -> list[SourceAdapter]:
    built = []
    for key, block in (sources_config or {}).items():
        block = block or {}
        if not block.get("enabled", False):
            continue
        cls = ADAPTERS.get(key)
        if cls is None:
            print(f"[adapters] WARNING: no adapter for source {key!r} — skipped")
            continue
        built.append(cls(block))
    return built


__all__ = ["SourceAdapter", "ADAPTERS", "build_adapters"]
