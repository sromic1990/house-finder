"""Standalone proof that the criteria engine filters + ranks correctly.

Run:  python demo_scoring.py
Uses hand-made sample listings (no scraping) so you can see exactly how your
config.yaml turns properties into a leaderboard.
"""
from __future__ import annotations

import sys

import yaml

# Windows terminals default to cp1252; force UTF-8 so €/·/✗ print cleanly.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from core.criteria import build_criteria
from core.models import Listing
from core.scoring import leaderboard


SAMPLES = [
    Listing(
        source="demo", source_id="1", url="http://example/1",
        title="Bright 3h in Kallio", price=420000, size_m2=78, rooms=3,
        lat=60.1840, lon=24.9500,
        features={
            "sauna": {"present": True, "private": True},
            "balcony": {"present": True, "glazed": True},
            "parking": {"type": "hall"},
        },
    ),
    Listing(
        source="demo", source_id="2", url="http://example/2",
        title="Cheap 2h, open parking", price=330000, size_m2=55, rooms=2,
        lat=60.2000, lon=24.9600,
        features={
            "sauna": {"present": True, "private": True},
            "balcony": {"present": False},
            "parking": {"type": "open"},
        },
    ),
    Listing(
        source="demo", source_id="3", url="http://example/3",
        title="Big 4h w/ garage, pricey", price=560000, size_m2=105, rooms=4,
        lat=60.1719, lon=24.9414,
        features={
            "sauna": {"present": True, "private": True},
            "balcony": {"present": True, "glazed": True},
            "parking": {"type": "garage"},
        },
    ),
    Listing(  # EXCLUDED: balcony not glazed
        source="demo", source_id="4", url="http://example/4",
        title="Nice 3h but open balcony", price=390000, size_m2=80, rooms=3,
        lat=60.1750, lon=24.9450,
        features={
            "sauna": {"present": True, "private": True},
            "balcony": {"present": True, "glazed": False},
            "parking": {"type": "garage"},
        },
    ),
    Listing(  # EXCLUDED: only shared sauna
        source="demo", source_id="5", url="http://example/5",
        title="Great value but shared sauna", price=310000, size_m2=70, rooms=3,
        lat=60.1700, lon=24.9400,
        features={
            "sauna": {"present": True, "private": False, "shared": True},
            "balcony": {"present": True, "glazed": True},
            "parking": {"type": "garage"},
        },
    ),
]


def main():
    with open("config.yaml", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    criteria = build_criteria(config["criteria"])

    ranked, excluded = leaderboard(SAMPLES, criteria, include_excluded=True)

    print("\n=== LEADERBOARD ===")
    for r in ranked:
        print(f"#{r.rank}  {r.score:5.1f}  {r.listing.title}")
        for line in r.explanation:
            print(f"        · {line}")

    print("\n=== EXCLUDED ===")
    for r in excluded:
        print(f"  ✗ {r.listing.title}  —  {r.exclude_reason}")


if __name__ == "__main__":
    main()
