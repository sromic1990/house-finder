# 🏠 Helsinki House Finder

A personal leaderboard that collects Helsinki apartments-for-sale from public
portals, ranks them against **your** criteria, shows them in an Etuovi-style web
UI, refreshes on a schedule (detecting new & delisted listings), and emails you
when something climbs into your top 5.

## What it does

- **Collects** listings from swappable source adapters (Etuovi, Oikotie, + a
  built-in `mock` source with sample data so it runs out of the box).
- **Filters + ranks** every listing with a pluggable criteria engine driven
  entirely by [`config.yaml`](config.yaml). Hard requirements exclude; soft
  preferences produce a weighted 0–100 score. #1 = your best match.
- **Tracks history** in SQLite: new listings get a `NEW` badge, and listings
  that vanish from their source are marked delisted.
- **Notifies** by email (Gmail) whenever a listing newly enters your top N.
- **Serves** a web UI with photos, floor plans, an OpenStreetMap map, key facts,
  and a per-listing "why this rank" score breakdown.

## Your criteria (current)

Hard requirements — a listing failing **any** of these is dropped (shown on the
Excluded page with the reason). Each is one toggle in `config.yaml`.

| Requirement | Criterion key |
|---|---|
| Total price ≤ €400,000 | `budget` |
| Living area ≥ 100 m² | `min_size` |
| Type: omakotitalo / rivitalo / paritalo | `property_type` |
| 3–5 bedrooms (excl. living room) | `bedrooms` |
| Private sauna | `sauna` |
| Duplex (two floors) | `duplex` |
| ≥ 2 toilets | `toilets` |
| Parking not an open spot w/ heating pole | `parking_min` |
| Built after 1975 | `built_after` |
| ≤ 1 km from a metro/train station | `station_distance` |
| Balcony (if any) must be glazed | `balcony_glazed` |
| Not asumisoikeus / part-ownership | `ownership_sanity` |

Ranking of the survivors (tune in `config.yaml`):
- **Land ownership** *(priority tier)*: every own-plot (oma tontti) house ranks
  above every leased-plot one; the score below orders within each tier. (Read
  from the detail page; leased-plot houses are shown, just ranked lower. To
  hard-exclude them instead, make `own_land` a filter — see git history.)
- **Parking** *(weight 4)*: garage > hall > covered > open+pole > open.
- **Transit** *(weight 4)*: walking distance to the nearest station.
- **Travel time** *(weight 3)*: shorter public-transport time to center/airport
  (a preference, not a hard cut; needs `DIGITRANSIT_KEY`).
- **Price**, **size**, **rooms** *(weight 2 each)*.

> This is a strict definition — expect a short list from real data. Loosen any
> line by editing its block (or `enabled: false`) in `config.yaml`.

### Travel time (routing)
The `travel_time` filter uses **Digitransit** (HSL's journey planner) for real
public-transport minutes to Helsinki center and the airport, computed at
collection time (`search.enrich_transit: true`) and cached per listing. It needs
a free `DIGITRANSIT_KEY` (see `.env.example`); without one, listings are kept
rather than routed. Station distance (`station_distance`) is straight-line and
needs no key.

## Host it online (free, private)
Publish to GitHub Pages with a passphrase-encrypted page, auto-refreshed every
6 h by GitHub Actions — accessible from any device. See **[DEPLOY.md](DEPLOY.md)**.

## Quick start

### Docker (recommended)
```bash
cp .env.example .env      # add GMAIL_USER + GMAIL_APP_PASSWORD for emails
docker compose up --build
# web UI:  http://localhost:8000
```
Two services start: `web` (the site) and `collector` (loops every
`schedule.interval_hours`, default 6h). They share a SQLite volume.

### Local (no Docker)
```bash
pip install -r requirements.txt
python -m collector.run --once     # one collection + ranking pass
uvicorn web.app:app --reload       # http://localhost:8000
```

## Configuration — everything lives in `config.yaml`
Change budget, min size, commute targets, parking preferences, weights, the
refresh interval, and the notification address there. No code changes needed.
Edit and restart to apply.

## Adding a new criterion later
The system is built for this. To add e.g. "prefer an elevator":
1. In `core/criteria/builtins.py` (or a new module), subclass `ScoreCriterion`
   or `FilterCriterion`, give it a unique `key`, decorate with `@register`.
2. Add a matching block under `criteria:` in `config.yaml`.

That's it — scoring, UI chips, and the score breakdown pick it up automatically.
See any existing criterion in `core/criteria/builtins.py` as a template.

## Languages
The UI is English by default and switchable to **Finnish** and **Swedish** via the
EN/FI/SV switch in the header (remembered with a cookie). Translations live in
[core/i18n.py](core/i18n.py) — add a language by adding its code + a block there.
Listing content (titles, districts) stays in its source language; the interface
chrome is what's translated.

## Email setup (Gmail)
Automated sending uses Gmail SMTP with an **App Password** (works headless):
Google Account → Security → 2-Step Verification → App passwords. Put the values
in `.env` as `GMAIL_USER` / `GMAIL_APP_PASSWORD`. Without them the collector
logs what it *would* send instead of failing.

## Status of the source adapters
- `oikotie` — ✅ **live**. Bootstraps the `OTA-*` tokens from the search page
  and queries the internal `/api/cards` JSON endpoint, filtered server-side to
  Helsinki house building-types (rivitalo/paritalo/omakotitalo) with the
  price/size pre-filters. Rich cards (year, coords, floor count, room code).
- `etuovi` — ✅ **live**. Parses `window.__INITIAL_STATE__` from the per-type
  search pages (`/myytavat-asunnot/helsinki/{type}`), paginating with `?sivu=N`.
- `mock` — sample Helsinki data for offline/demo use (disabled by default; flip
  on in `config.yaml`).

**How much they return:** a full run fetches ~800 Helsinki houses; the strict
criteria typically leave ~15–30 on the leaderboard. Tune `etuovi.max_pages` /
`oikotie.building_types` in `config.yaml`.

### Known limitations (real data)
- **Price = debt-free (velaton) total.** For most listings the list endpoints
  give the right figure. Right-of-occupancy / part-ownership (asumisoikeus,
  osaomistus) listings quote a share price instead — these are dropped by the
  `ownership_sanity` filter (a €/m² floor) and, for Etuovi, by skipping
  non-numeric listing IDs. A handful of edge cases may slip through; the detail
  page always shows the authoritative price (linked from each card).
- **Feature parsing is text-based.** sauna / toilets / balcony glazing come from
  the Finnish room code (`4h+k+2xwc+kph+s`, `s`=sauna). When a field isn't stated
  it stays *unknown* and the criterion's `on_unknown` policy applies.
- **Parking**: the only deal-breaker is an **open spot with a heating pole**
  (`open_pole`) or no parking. Garage, hall, covered/carport, and a plain/own
  dedicated spot all pass; roofed options rank higher via the parking scorer.
  Parking type is read from the room code (`at`=garage, `ah`=hall, `tolp`=pole)
  and, for Oikotie, the unit-scoped "huoneiston autopaikasta" sentence (company/
  neighbour parking in the prose is ignored to avoid false credit). When a
  listing doesn't state parking it stays *unknown* and is **kept** — so nothing
  is wrongly dropped; only a confirmed open+pole/none is excluded.
- **Fragile by nature.** No public API exists; these read internal endpoints /
  embedded state and can break when the sites change. Personal, low-volume use
  only — against both sites' ToS. Each adapter is isolated and fails soft.

## Legal / fair-use note
These portals have no public API and scraping is against their Terms of Service.
This is a low-volume, rate-limited **personal** tool. Don't redistribute the data
or run it aggressively. Adapters are isolated so you can remove any source easily.

## Layout
```
config.yaml            your criteria + schedule + notifications
core/                  models, criteria engine, scoring, notify, config
collector/             source adapters + normalization + scheduler
db/                    SQLite store + history/delisted tracking
web/                   FastAPI app + templates + styles
tests/                 parser tests (python tests/test_normalize.py)
demo_scoring.py        prove the ranking with sample data, no scraping
```
