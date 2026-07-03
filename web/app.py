"""Local web server — serves the same rich single-page leaderboard as the hosted
site (map/list split, live criteria filters, detail modal), generated live from
the shared SQLite DB on each request. Unencrypted, since this runs locally
(docker compose / uvicorn). The hosted build is the encrypted static file from
web/staticgen.py; this just renders the same page dynamically for local use.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from core.config import load_config
from db import Store
from web.staticgen import build_site

app = FastAPI(title="House Finder")


@app.get("/", response_class=HTMLResponse)
def index():
    config = load_config()
    store = Store(config.get("db", {}).get("path", "db/house_finder.sqlite3"))
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return build_site(config, store, generated)


@app.get("/healthz")
def healthz():
    return {"ok": True}
