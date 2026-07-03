"""SQLite persistence + history tracking.

Responsibilities:
  * upsert listings seen in a collection run (keeps first_seen / last_seen)
  * mark listings delisted when a source stops returning them
  * store leaderboard snapshots so we can detect when something newly enters
    the top N (drives the email notification)

SQLite is used so the whole app has zero external DB dependencies inside Docker.
Listing bodies are stored as JSON; the columns we filter/sort on are promoted.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from core.models import Listing


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    uid          TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    url          TEXT,
    title        TEXT,
    price        REAL,
    size_m2      REAL,
    rooms        REAL,
    first_seen   TEXT,
    last_seen    TEXT,
    delisted     INTEGER DEFAULT 0,
    delisted_at  TEXT,
    data         TEXT NOT NULL          -- full Listing as JSON
);

CREATE TABLE IF NOT EXISTS rank_snapshots (
    taken_at     TEXT NOT NULL,
    uid          TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    score        REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snap_uid ON rank_snapshots(uid);
CREATE INDEX IF NOT EXISTS idx_listings_delisted ON listings(delisted);
"""


class Store:
    def __init__(self, path: str = "db/house_finder.sqlite3"):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- listing upsert / history ------------------------------------
    def upsert_listings(self, listings: Iterable[Listing]) -> dict:
        """Insert/update listings from one run. Returns {new, updated}."""
        now = _now()
        new_uids, updated_uids = [], []
        with self._conn() as c:
            for lst in listings:
                lst.last_seen = now
                row = c.execute(
                    "SELECT first_seen FROM listings WHERE uid=?", (lst.uid,)
                ).fetchone()
                if row is None:
                    lst.first_seen = now
                    lst.delisted = False
                    new_uids.append(lst.uid)
                else:
                    lst.first_seen = row["first_seen"]
                    updated_uids.append(lst.uid)
                c.execute(
                    """INSERT INTO listings
                       (uid, source, source_id, url, title, price, size_m2, rooms,
                        first_seen, last_seen, delisted, delisted_at, data)
                       VALUES (?,?,?,?,?,?,?,?,?,?,0,NULL,?)
                       ON CONFLICT(uid) DO UPDATE SET
                        url=excluded.url, title=excluded.title, price=excluded.price,
                        size_m2=excluded.size_m2, rooms=excluded.rooms,
                        last_seen=excluded.last_seen, delisted=0, delisted_at=NULL,
                        data=excluded.data""",
                    (lst.uid, lst.source, lst.source_id, lst.url, lst.title,
                     lst.price, lst.size_m2, lst.rooms, lst.first_seen,
                     lst.last_seen, json.dumps(lst.to_dict(), ensure_ascii=False)),
                )
        return {"new": new_uids, "updated": updated_uids}

    def mark_delisted(self, seen_uids: set[str], sources: set[str]) -> list[str]:
        """Any active listing from `sources` NOT in `seen_uids` is now delisted."""
        now = _now()
        newly = []
        placeholders = ",".join("?" * len(sources)) or "''"
        with self._conn() as c:
            rows = c.execute(
                f"SELECT uid FROM listings WHERE delisted=0 AND source IN ({placeholders})",
                tuple(sources),
            ).fetchall()
            for r in rows:
                if r["uid"] not in seen_uids:
                    c.execute(
                        "UPDATE listings SET delisted=1, delisted_at=? WHERE uid=?",
                        (now, r["uid"]),
                    )
                    newly.append(r["uid"])
        return newly

    # ---- reads --------------------------------------------------------
    def active_listings(self) -> list[Listing]:
        with self._conn() as c:
            rows = c.execute("SELECT data FROM listings WHERE delisted=0").fetchall()
        return [self._row_to_listing(r["data"]) for r in rows]

    def all_listings(self, include_delisted=True) -> list[Listing]:
        q = "SELECT data FROM listings" if include_delisted else \
            "SELECT data FROM listings WHERE delisted=0"
        with self._conn() as c:
            rows = c.execute(q).fetchall()
        return [self._row_to_listing(r["data"]) for r in rows]

    @staticmethod
    def _row_to_listing(data_json: str) -> Listing:
        d = json.loads(data_json)
        allowed = {f.name for f in fields(Listing)}
        return Listing(**{k: v for k, v in d.items() if k in allowed})

    # ---- rank snapshots (for top-N change detection) -----------------
    def previous_ranks(self) -> dict[str, int]:
        """uid -> rank from the most recent snapshot (empty on first run)."""
        with self._conn() as c:
            last = c.execute(
                "SELECT taken_at FROM rank_snapshots ORDER BY taken_at DESC LIMIT 1"
            ).fetchone()
            if not last:
                return {}
            rows = c.execute(
                "SELECT uid, rank FROM rank_snapshots WHERE taken_at=?",
                (last["taken_at"],),
            ).fetchall()
        return {r["uid"]: r["rank"] for r in rows}

    def save_snapshot(self, ranked) -> None:
        now = _now()
        with self._conn() as c:
            c.executemany(
                "INSERT INTO rank_snapshots (taken_at, uid, rank, score) VALUES (?,?,?,?)",
                [(now, r.listing.uid, r.rank, r.score) for r in ranked],
            )
