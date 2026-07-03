"""Collector orchestrator + scheduler.

One collection run:
  1. fetch listings from every enabled source adapter
  2. upsert into the store (tracks first_seen / last_seen)
  3. mark listings that disappeared from their source as delisted
  4. rank active listings against config.criteria
  5. email if anything newly entered the top N
  6. snapshot the ranking (so the next run can detect top-N changes)

Usage:
  python -m collector.run --once      # single run then exit
  python -m collector.run             # loop forever, every interval_hours
"""
from __future__ import annotations

import argparse
import logging
import os
import time

try:  # load .env for local runs (Docker uses compose env_file)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from core.config import load_config
from core.criteria import build_criteria
from core.notify import notify_new_top_entries, notify_price_drops
from core.routing import transit_minutes
from core.scoring import rank_listings
from db import Store

from .adapters import build_adapters
from .enrich import better_parking, fetch_detail

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("collector")


def collect_once(config: dict, store: Store) -> dict:
    search = config.get("search", {})
    adapters = build_adapters(config.get("sources", {}))
    if not adapters:
        log.warning("no enabled sources — nothing to collect")
        return {}

    all_listings, active_sources = [], set()
    for ad in adapters:
        active_sources.add(ad.name)
        found = ad.fetch(search)
        log.info("source %s: %d listings", ad.name, len(found))
        all_listings.extend(found)

    # dedup by uid (a source shouldn't return dupes, but be safe)
    by_uid = {l.uid: l for l in all_listings}
    listings = list(by_uid.values())

    # Apartment blocks (kerrostalo) are only wanted when the flat is a confirmed
    # duplex (two-floor / maisonette); drop the rest so they never enter the pool.
    listings = [l for l in listings
                if l.property_type != "kerrostalo" or l.features.get("duplex") is True]

    changes = store.upsert_listings(listings)
    delisted = store.mark_delisted(set(by_uid), active_sources)
    log.info("upsert: %d new, %d updated, %d delisted",
             len(changes["new"]), len(changes["updated"]), len(delisted))

    criteria = build_criteria(config.get("criteria", {}))
    active = store.active_listings()

    # PRELIM rank without the detail-dependent parking filter, so candidates
    # whose parking isn't in the list data still get a chance to be enriched
    # (parking is often only on the detail page, not the room code).
    prelim_criteria = [c for c in criteria if c.key != "parking_min"]
    candidates = rank_listings(active, prelim_criteria)
    log.info("prelim: %d candidates (of %d fetched)", len(candidates), len(active))

    # Detail-enrich candidates (cheap: dozens of fetches) with data absent from
    # the list endpoints: land ownership, confirmed parking, and travel time.
    enriched = []
    for r in candidates:
        L = r.listing
        changed = False
        if "land_ownership" not in L.features or "parking" not in L.features:
            detail = fetch_detail(L.url)
            if detail.get("land_ownership") and "land_ownership" not in L.features:
                L.features["land_ownership"] = detail["land_ownership"]
                changed = True
            if detail.get("parking"):
                best = better_parking(L.features.get("parking"), detail["parking"])
                if best != L.features.get("parking"):
                    L.features["parking"] = best
                    changed = True
        if search.get("enrich_transit") and not L.features.get("transit_minutes") \
                and L.lat is not None:
            tm = transit_minutes(L.lat, L.lon)
            if tm:
                L.features["transit_minutes"] = tm
                changed = True
        if changed:
            enriched.append(L)
    if enriched:
        store.upsert_listings(enriched)

    # FINAL rank with ALL filters — strict indoor-parking now applies to the
    # enriched data, so anything without a confirmed garage/hall is excluded.
    ranked = rank_listings(store.active_listings(), criteria)
    log.info("ranked %d listings (of %d fetched) after enrichment", len(ranked), len(active))

    # notify on new top-N entrants (compare to previous snapshot)
    prev = store.previous_ranks()
    board_url = os.getenv("PUBLIC_URL") or config.get("web", {}).get("public_url", "http://localhost:8000")
    fired = notify_new_top_entries(prev, ranked, config=config, board_url=board_url)
    if fired:
        log.info("notified: %d new top-%s entr%s",
                 len(fired), config["notify"]["email"].get("top_n", 5),
                 "y" if len(fired) == 1 else "ies")

    # price-drop alerts for qualifying (ranked) listings cut this run
    dropped_uids = {u for u, _, _ in changes.get("dropped", [])}
    board_dropped = [r for r in ranked if r.listing.uid in dropped_uids]
    if board_dropped:
        notify_price_drops(board_dropped, config=config, board_url=board_url)
        log.info("price-drop email: %d board listing(s) cut this run", len(board_dropped))

    # Render the static site BEFORE saving this run's snapshot, so the site's
    # NEW badges compare against the PREVIOUS run (like the email does).
    if os.getenv("PUBLISH_SITE"):
        from web.staticgen import write_site
        path = write_site(config, store, os.getenv("SITE_DIR", "site"))
        log.info("rendered static site -> %s%s", path,
                 " (encrypted)" if os.getenv("SITE_PASSWORD") else "")

    store.save_snapshot(ranked)
    return {"new": changes["new"], "delisted": delisted,
            "ranked": len(ranked), "notified": len(fired)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = ap.parse_args()

    config = load_config()
    store = Store(config.get("db", {}).get("path", "db/house_finder.sqlite3"))

    if args.once:
        collect_once(config, store)
        return

    interval_h = float(config.get("schedule", {}).get("interval_hours", 6))
    log.info("collector loop started; interval = %sh", interval_h)
    while True:
        try:
            collect_once(config, store)
        except Exception:
            log.exception("collection cycle failed; will retry next interval")
        time.sleep(interval_h * 3600)


if __name__ == "__main__":
    main()
