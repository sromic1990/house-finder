"""Render the leaderboard to ONE self-contained, responsive HTML file.

Everything (data + CSS + JS + i18n) is embedded so it can be served as a single
static asset (GitHub Pages) and, optionally, AES-encrypted (see encrypt.py).

The page ships the full candidate pool (all fetched houses within a browse
envelope) annotated with each criterion's pass/fail + per-scorer values, and
does filtering + ranking CLIENT-SIDE. That powers: live criteria filters (toggle
any criterion), an Airbnb-style map/list split (markers with a photo preview;
the list shows only what's in the current map viewport), and a plain grid when
the map is off. All with no server.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path

from core.criteria import build_criteria
from core.criteria.builtins import TransitProximity, haversine_km
from core.i18n import LANG_NAMES, TRANSLATIONS
from core.cost import cost_to_own
from core.risk import bank_risk, renovation_risk

BASE = Path(__file__).parent

_PARK_RANK = {"garage": 6, "hall": 5, "covered": 4, "own_spot": 3,
              "open_pole": 2, "open": 1, "none": 0}

# how strongly low risk pulls "Best match" up (relative to the other scorer weights)
RENO_WEIGHT = 3
BANK_WEIGHT = 2


def _station_km(L):
    if L.lat is None or L.lon is None:
        return None
    return round(min(haversine_km(L.lat, L.lon, s[1], s[2])
                     for s in TransitProximity.DEFAULT_STATIONS), 2)


def _in_envelope(L, browse) -> bool:
    """Bound the browse pool so relaxing filters can't dump €2M mansions / tiny flats."""
    if L.price is not None and L.price > browse["price_max"]:
        return False
    if L.size_m2 is not None and L.size_m2 < browse["size_min"]:
        return False
    return True


# Fields that make a listing trustworthy to act on. label -> present? predicate.
_CONFIDENCE_FIELDS = [
    ("price", lambda L: L.price is not None),
    ("size", lambda L: L.size_m2 is not None),
    ("build year", lambda L: L.year_built is not None),
    ("rooms", lambda L: L.rooms is not None),
    ("location", lambda L: L.lat is not None and L.lon is not None),
    ("sauna", lambda L: L.features.get("sauna") is not None),
    ("parking", lambda L: L.features.get("parking") is not None),
    ("plot ownership", lambda L: L.features.get("land_ownership") is not None),
    ("maintenance charge", lambda L: L.features.get("maintenance") is not None),
    ("energy class", lambda L: L.features.get("energy_class") is not None),
]


def _confidence(L) -> dict:
    """How complete/verifiable this listing's data is: a % plus what's unknown."""
    missing = [label for label, ok in _CONFIDENCE_FIELDS if not ok(L)]
    known = len(_CONFIDENCE_FIELDS) - len(missing)
    pct = round(100 * known / len(_CONFIDENCE_FIELDS))
    level = "high" if pct >= 80 else "medium" if pct >= 55 else "low"
    return {"pct": pct, "level": level, "missing": missing}


def _effective_history(L) -> list:
    """Observed price points [[iso, price], ...]. Falls back to the tracked drop
    (prev_price -> price) when the accumulated series isn't populated yet."""
    hist = list(L.price_history or [])
    if hist:
        return hist
    if L.prev_price is not None and L.price_dropped_at:
        return [[L.first_seen or L.price_dropped_at, L.prev_price],
                [L.price_dropped_at, L.price]]
    if L.price is not None:
        return [[L.first_seen, L.price]]
    return []


def _candidate_payload(L, filters, scorers, prev, year_now, medians, type_medians, overall, cost_cfg) -> dict:
    tm = L.features.get("transit_minutes") or {}
    reno = renovation_risk(L, year_now)
    city = (L.city or "").strip().capitalize()
    # prefer the same-type median; fall back to city-wide, then overall
    med = type_medians.get((city, L.property_type)) or medians.get(city) or overall
    bank = bank_risk(L, med, reno["level"])
    cost = cost_to_own(L, reno["score"], cost_cfg)
    passes = {}
    for f in filters:
        try:
            passes[f.key] = (f.check(L).passed if f.applies(L) else True)
        except Exception:
            passes[f.key] = True
    contribs = []
    for s in scorers:
        if s.applies(L):
            c = s.score(L)
            if c.applicable:
                contribs.append({"key": s.key, "label": c.label,
                                 "raw": round(c.raw, 3), "weight": c.weight,
                                 "prio": bool(getattr(s, "is_priority", False))})
    # fold the two risk flags into the weighted score (safer = higher raw)
    contribs.append({"key": "reno_safety", "label": f"renovation risk: {reno['level']}",
                     "raw": round(1 - reno["score"], 3), "weight": RENO_WEIGHT, "prio": False})
    contribs.append({"key": "bank_safety", "label": f"bank risk: {bank['level']}",
                     "raw": round(1 - bank["score"], 3), "weight": BANK_WEIGHT, "prio": False})
    return {
        "id": L.uid, "source": L.source, "url": L.url, "title": L.title,
        "price": L.price, "ppm2": L.price_per_m2, "size": L.size_m2,
        "area_ppm2": round(med) if med else None,   # bank's reference: median €/m² for this type nearby
        "rooms": L.rooms, "bedrooms": L.bedrooms, "year": L.year_built,
        "floor": L.floor, "type": L.property_type,
        "address": L.address, "district": L.district,
        "city": (L.city or "").strip().capitalize(),   # normalize "HELSINKI" -> "Helsinki"
        "lat": L.lat, "lon": L.lon,
        "photos": L.photos, "floor_plans": L.floor_plans, "features": L.features,
        "transit_min": min(tm.values()) if tm else None,
        "station_km": _station_km(L),
        "parking_rank": _PARK_RANK.get((L.features.get("parking") or {}).get("type")),
        "new": L.uid not in prev,
        "prev_price": L.prev_price, "dropped_at": L.price_dropped_at,
        "first_seen": L.first_seen, "last_seen": L.last_seen,
        "price_history": _effective_history(L), "confidence": _confidence(L),
        "pass": passes, "contribs": contribs,
        "reno_risk": reno, "bank_risk": bank, "cost": cost,
        "reno_planned": L.features.get("reno_planned"),
        "reno_done": L.features.get("reno_done"),
    }


def build_site(config: dict, store, generated: str) -> str:
    criteria = build_criteria(config.get("criteria", {}))
    filters = [c for c in criteria if c.kind == "filter"]
    scorers = [c for c in criteria if c.kind == "score"]
    browse = {"price_max": 600000, "size_min": 60, **config.get("web", {}).get("browse", {})}
    prev = store.previous_ranks()

    pool = [L for L in store.active_listings() if _in_envelope(L, browse)]
    # Median €/m² so bank-risk can flag over-market pricing. Compared within the
    # SAME property type (a detached house's €/m² isn't comparable to a flat's);
    # a city-wide median mixing all types would flag normal houses as "expensive".
    by_city, by_ct = {}, {}
    for L in pool:
        if L.price_per_m2 and L.city:
            c = L.city.strip().capitalize()
            by_city.setdefault(c, []).append(L.price_per_m2)
            if L.property_type:
                by_ct.setdefault((c, L.property_type), []).append(L.price_per_m2)
    medians = {c: statistics.median(v) for c, v in by_city.items() if v}
    # type-specific median only when the bucket is big enough to be meaningful
    type_medians = {k: statistics.median(v) for k, v in by_ct.items() if len(v) >= 8}
    overall = statistics.median([p for v in by_city.values() for p in v]) if by_city else None
    year_now = datetime.now(timezone.utc).year
    cost_cfg = config.get("web", {}).get("cost", {})
    listings = [_candidate_payload(L, filters, scorers, prev, year_now, medians, type_medians, overall, cost_cfg)
                for L in pool]

    data = {
        "title": config.get("web", {}).get("title", "House Leaderboard"),
        "generated": generated,
        "top_n": config.get("notify", {}).get("email", {}).get("top_n", 5),
        "listings": listings,
        "criteria": ([{"key": c.key, "title": c.title, "kind": "filter"} for c in filters]
                     + [{"key": c.key, "title": c.title, "kind": "score",
                         "weight": c.weight, "priority": c.is_priority} for c in scorers]
                     + [{"key": "reno_safety", "title": "Low renovation risk (5y)",
                         "kind": "score", "weight": RENO_WEIGHT, "priority": False},
                        {"key": "bank_safety", "title": "Low bank risk",
                         "kind": "score", "weight": BANK_WEIGHT, "priority": False}]),
        # Manual-refresh button config — embedded ONLY when both a dispatch token
        # and a site passphrase are present (i.e. the output will be encrypted),
        # so the token never lands in a plaintext page.
        "dispatch": ({"repo": os.getenv("GH_REPO", ""),
                      "workflow": os.getenv("GH_WORKFLOW", "update.yml"),
                      "ref": os.getenv("GH_REF", "main"),
                      "token": os.getenv("GH_DISPATCH_TOKEN")}
                     if os.getenv("GH_DISPATCH_TOKEN") and os.getenv("SITE_PASSWORD") else None),
        "i18n": TRANSLATIONS,
        "langs": LANG_NAMES,
    }
    css = (BASE / "static" / "style.css").read_text(encoding="utf-8") + _EXTRA_CSS
    # gzip + base64 the data so the (un-gzippable, encrypted) page stays small
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    b64 = base64.b64encode(gzip.compress(raw, 6)).decode()
    return _TEMPLATE.replace("/*CSS*/", css).replace("__DATA_B64__", b64)


def write_site(config: dict, store, out_dir: str = "site") -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = build_site(config, store, generated)
    pw = os.getenv("SITE_PASSWORD")
    if pw:
        from web.encrypt import lock_html
        html = lock_html(html, pw)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return str(out / "index.html")


def main():
    from core.config import load_config
    from db import Store
    cfg = load_config()
    store = Store(cfg.get("db", {}).get("path", "db/house_finder.sqlite3"))
    path = write_site(cfg, store)
    print(f"wrote {path}" + (" (encrypted)" if os.getenv("SITE_PASSWORD") else ""))


_EXTRA_CSS = """
.stamp{color:var(--muted);font-size:12px}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:14px 0}
.controls select,.controls button{padding:8px 12px;border-radius:9px;border:1px solid var(--line);background:#fff;font-size:14px;font-weight:600;color:var(--ink);cursor:pointer}
.controls button.active{background:var(--ink);color:#fff;border-color:var(--ink)}
.controls label{font-size:13px;color:var(--muted);font-weight:600}
.count{color:var(--muted);font-size:13px;margin-left:auto}
.rankbar,.citybar{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin:0 0 12px}
.bar-label{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-right:2px}
.rk,.ct,.tp{font-size:13px;font-weight:600;padding:6px 11px;border-radius:999px;border:1px solid var(--line);background:#fff;color:var(--muted);cursor:pointer;user-select:none}
.rk.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.ct.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.tp.on{background:#7048e8;color:#fff;border-color:#7048e8}
.rk:hover,.ct:hover,.tp:hover{border-color:var(--ink)}
.filters-panel{display:none;flex-wrap:wrap;gap:8px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px}
.filters-panel.open{display:flex}
.filters-panel .grp{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);width:100%;margin:6px 0 0}
.chk{display:inline-flex;align-items:center;gap:6px;font-size:13px;background:#f1f3f6;padding:6px 10px;border-radius:999px;cursor:pointer;user-select:none}
.chk input{accent-color:var(--top);margin:0}
.chk.off{opacity:.45}
.split{display:flex;gap:16px;align-items:flex-start}
.map-pane{position:sticky;top:64px;flex:1.15;height:calc(100vh - 84px)}
.map-pane #bigmap{width:100%;height:100%;border-radius:14px;border:1px solid var(--line)}
.list-pane{flex:1;min-width:0}
.list-pane .grid{grid-template-columns:repeat(auto-fill,minmax(220px,1fr))}
@media(max-width:860px){.split{flex-direction:column}.map-pane{position:relative;top:0;width:100%;height:56vh;flex:none}}
.price-pill{background:#fff;border:1px solid rgba(0,0,0,.18);border-radius:999px;box-shadow:0 1px 5px rgba(0,0,0,.28);padding:3px 9px;font-weight:800;font-size:12.5px;color:#16181d;white-space:nowrap;transform:translate(-50%,-115%)}
.price-pill.top{background:var(--top);color:#fff;border-color:var(--top)}
.leaflet-popup-content{margin:0}.leaflet-popup-content-wrapper{padding:0;border-radius:12px;overflow:hidden}
.mk{display:flex;gap:0;width:270px}
.mk img{width:100px;height:104px;object-fit:cover;flex:none}
.mk .i{padding:9px 10px;min-width:0}
.mk .i .p{font-weight:800}.mk .i .t{font-size:13px;color:#333;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:1px 0}
.mk .i .f{font-size:12px;color:var(--muted);margin-bottom:6px}
.mk .i button{font-size:12px;font-weight:800;color:var(--accent);background:none;border:0;padding:0;cursor:pointer}
.modal-back{position:fixed;inset:0;background:rgba(10,12,16,.55);display:none;align-items:flex-start;justify-content:center;z-index:1000;overflow-y:auto;padding:30px 14px}
.modal-back.open{display:flex}
.modal{background:var(--bg);max-width:900px;width:100%;border-radius:16px;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.3)}
.modal-inner{padding:20px 22px 30px}
.modal-close{position:sticky;top:0;float:right;background:#fff;border:1px solid var(--line);width:34px;height:34px;border-radius:50%;cursor:pointer;font-size:18px}
.excluded-hint{color:var(--muted);font-size:13px;margin:8px 0 0}
.cost-line{margin-top:6px;font-weight:800;font-size:14px;color:#1a1a1a}
.cost-line .cost-sub{font-weight:600;font-size:11.5px;color:var(--muted)}
.cost-box{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:24px}
.cost-box h2{font-size:16px;margin:2px 0 8px}
.cost-big{font-size:24px;font-weight:800;margin-bottom:10px}
.cost-box table{width:100%;border-collapse:collapse;margin-bottom:8px}
.cost-box th{text-align:left;color:var(--muted);font-weight:500;padding:5px 0}
.cost-box td{text-align:right;padding:5px 0;font-weight:600}
.cost-box tr.cost-info th,.cost-box tr.cost-info td{color:var(--muted);font-weight:500;border-top:1px dashed var(--line);padding-top:7px}
.reno-list{font-size:13px;color:#444;margin:6px 0;line-height:1.45}
.drop-badge{position:absolute;bottom:10px;left:10px;background:#e03131;color:#fff;font-weight:800;font-size:11px;padding:3px 8px;border-radius:6px}
.drop-was{color:#e03131;font-weight:700;font-size:12.5px}
.risks{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.risk{font-size:11.5px;font-weight:700;padding:3px 8px;border-radius:7px;border:1px solid}
.risk-box{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:24px}
.risk-box h2{font-size:16px;margin:2px 0 12px}
.risk-row{margin-bottom:10px}
.risk-h{font-weight:800;font-size:14px;margin-bottom:2px}
.risk-row ul{margin:2px 0 0;padding-left:18px;color:#444}
.risk-row li{font-size:13px;margin:2px 0}
.risk-disc{color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:10px;margin-top:6px}
.trust{display:flex;gap:14px;align-items:center;margin-top:8px;font-size:12px;color:var(--muted);font-weight:600}
.trust .conf{display:inline-flex;align-items:center;gap:5px}
.trust .dot{width:8px;height:8px;border-radius:50%;background:#0f9d58;display:inline-block}
.trust .conf-medium{color:#c6741f}.trust .conf-medium .dot{background:#e8892b}
.trust .conf-low{color:#d9480f}.trust .conf-low .dot{background:#e03131}
.hist-box{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:24px}
.hist-box h2{font-size:16px;margin:2px 0 12px}
.hist-block{margin-bottom:12px}
.spark{width:100%;height:52px;display:block;background:linear-gradient(180deg,transparent,rgba(0,0,0,.03));border-radius:8px}
.hist-sub{font-size:13px;color:var(--muted);margin-top:6px}
.hist-tbl{width:100%;border-collapse:collapse}
.hist-tbl th{text-align:left;color:var(--muted);font-weight:500;padding:5px 0;width:58%}
.hist-tbl td{text-align:right;padding:5px 0;font-weight:600}
.hist-missing{color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:10px;margin-top:8px}
.cmp-btn{position:absolute;bottom:10px;right:10px;width:30px;height:30px;border-radius:50%;border:1.5px solid #fff;background:rgba(17,24,32,.62);color:#fff;font-size:17px;font-weight:700;cursor:pointer;display:grid;place-items:center;line-height:1;z-index:3;padding:0}
.cmp-btn:hover{transform:scale(1.12)}
.cmp-btn.on{background:var(--accent);border-color:#fff}
.card.cmp-on{outline:2px solid var(--accent);outline-offset:-1px}
.compare-bar{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:1500;display:none;align-items:center;gap:12px;background:#12151b;color:#fff;border-radius:999px;padding:9px 9px 9px 18px;box-shadow:0 12px 34px rgba(0,0,0,.4)}
.compare-bar.show{display:flex}
.compare-bar .cb-label{font-size:13px;font-weight:600;color:#c9ced8;white-space:nowrap}
.compare-bar .cb-thumbs{display:flex}
.compare-bar .cb-thumbs img{width:34px;height:34px;border-radius:8px;object-fit:cover;border:2px solid #12151b;margin-left:-8px}
.compare-bar .cb-go{background:var(--top);color:#fff;border:0;border-radius:999px;padding:9px 18px;font-weight:700;font-size:14px;cursor:pointer}
.compare-bar .cb-go:disabled{opacity:.5;cursor:default}
.compare-bar .cb-clear{background:none;border:0;color:#c9ced8;font-size:13px;cursor:pointer;padding:0 6px}
.cmp-modal{max-width:1080px}
.cmp-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:12px}
.cmp-table{width:100%;border-collapse:collapse;font-size:13.5px}
.cmp-table th,.cmp-table td{padding:10px 13px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
.cmp-table tr:last-child td{border-bottom:0}
.cmp-metric{color:var(--muted);font-weight:600;white-space:nowrap;position:sticky;left:0;background:var(--bg)}
.cmp-table td.cmp-best{color:var(--top);font-weight:800}
.cmp-col-head{min-width:158px;font-weight:400}
.cmp-col-head img{width:100%;height:92px;object-fit:cover;border-radius:8px;margin-bottom:6px;display:block}
.cmp-col-head .ch-title{font-weight:700;font-size:13px;line-height:1.3;margin-bottom:4px}
.cmp-col-head .ch-link{display:block;font-size:12px;color:var(--accent);font-weight:600;margin-bottom:4px}
.cmp-col-head .ch-rm{color:var(--new);cursor:pointer;font-size:12px;border:0;background:none;padding:0}
.viewbar{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px}
.vw{font-size:13.5px;font-weight:700;padding:7px 14px;border-radius:999px;border:1px solid var(--line);background:#fff;color:var(--muted);cursor:pointer;user-select:none}
.vw b{color:var(--ink);margin-left:2px}
.vw.on{background:var(--ink);color:#fff;border-color:var(--ink)}.vw.on b{color:#fff}
.mark-actions{display:flex;gap:8px;align-items:center;margin-top:10px}
.mk-btn{font-size:12.5px;font-weight:700;padding:6px 11px;border-radius:8px;border:1px solid var(--line);background:#fff;color:var(--ink);cursor:pointer}
.mk-btn:hover{border-color:var(--ink)}
.mk-save.on{background:#fff7e0;border-color:#f5b301;color:#8a6100}
.mk-dismiss.on{background:#fdecec;border-color:#e03131;color:#b02020}
.mk-note-flag{font-size:14px;margin-left:auto}
.card.saved{outline:2px solid #f5b301;outline-offset:-1px}
.card.dismissed{opacity:.5}
.detail-marks{display:flex;gap:10px;margin:12px 0 0}
.detail-marks .mk-btn{font-size:14px;padding:9px 16px}
.note-box{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:24px}
.note-box h2{font-size:16px;margin:2px 0 10px}
.note-box textarea{width:100%;min-height:82px;border:1px solid var(--line);border-radius:9px;padding:10px;font:inherit;font-size:14px;resize:vertical;background:var(--bg);color:var(--ink)}
#refreshBtn{background:var(--top);color:#fff;border-color:var(--top)}
#refreshBtn:disabled{opacity:.6;cursor:default}
.rf-overlay{position:fixed;inset:0;background:rgba(8,10,14,.74);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;z-index:2000}
.rf-overlay.open{display:flex}
.rf-card{background:#12151b;color:#e8eaed;border:1px solid #262b34;border-radius:20px;padding:34px 30px;width:min(390px,92vw);text-align:center;box-shadow:0 30px 80px rgba(0,0,0,.55)}
.rf-radar{width:112px;height:112px;margin:0 auto 16px;position:relative;display:grid;place-items:center}
.rf-radar span{font-size:46px;z-index:2;animation:rfpulse 1.6s ease-in-out infinite}
.rf-radar::before,.rf-radar::after{content:"";position:absolute;inset:0;border-radius:50%;border:2px solid var(--top);opacity:0;animation:rfring 2s ease-out infinite}
.rf-radar::after{animation-delay:1s}
@keyframes rfring{0%{transform:scale(.35);opacity:.85}100%{transform:scale(1);opacity:0}}
@keyframes rfpulse{0%,100%{transform:scale(1)}50%{transform:scale(1.13)}}
.rf-title{font-size:19px;font-weight:800}
.rf-status{color:#9aa1ab;margin:6px 0 16px;min-height:20px}
.rf-status.rf-err{color:#ff6b6b}
.rf-steps{display:flex;justify-content:center;gap:7px;margin-bottom:16px;flex-wrap:wrap}
.rf-steps .s{font-size:11.5px;font-weight:700;color:#6b7280;background:#1b1f27;border:1px solid #262b34;border-radius:999px;padding:5px 10px}
.rf-steps .s.active{color:#fff;border-color:var(--top)}
.rf-steps .s.done{color:var(--top);border-color:var(--top)}
.rf-bar{height:6px;background:#1b1f27;border-radius:999px;overflow:hidden;position:relative}
.rf-bar>span{position:absolute;height:100%;width:35%;background:linear-gradient(90deg,transparent,var(--top),transparent);animation:rfbar 1.4s linear infinite;border-radius:999px}
@keyframes rfbar{0%{left:-40%}100%{left:105%}}
.rf-wait{color:#6b7280;font-size:12px;margin-top:14px}
.rf-close{margin-top:14px;background:none;border:0;color:#9aa1ab;font-size:13px;cursor:pointer;text-decoration:underline}

/* =====================================================================
   MOBILE — scoped so the desktop view is byte-for-byte unchanged. Placed
   LAST in the cascade so it wins over base + _EXTRA_CSS on shared classes.
   Goals: zero horizontal scroll + a native-app feel on phones.
   ===================================================================== */
/* Safe global guards (no visual effect on desktop): never scroll sideways;
   never let an image or long word push the layout wider than the screen. */
html,body{max-width:100%;overflow-x:hidden}
body{-webkit-text-size-adjust:100%}
img{max-width:100%}
.card-title,.board-intro h1,.detail-addr,.detail-head h1{overflow-wrap:anywhere}
.list-pane,.map-pane,.card-body{min-width:0}
@media(max-width:640px){
  *{-webkit-tap-highlight-color:transparent}
  input,select,textarea{font-size:16px}
  main{padding:14px 12px calc(48px + env(safe-area-inset-bottom))}
  .site-header{padding:11px 14px;padding-top:calc(11px + env(safe-area-inset-top));gap:8px}
  .brand{font-size:15px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .site-header nav{flex:none}
  .site-header nav>a{margin-left:10px;font-size:14px}
  .langs{margin-left:10px}
  .board-intro h1{font-size:20px}
  .board-intro p{font-size:13px}
  .grid,.list-pane .grid{grid-template-columns:1fr;gap:14px}
  .card{border-radius:16px}
  .card:hover{transform:none}
  .card:active{transform:scale(.985);transition:transform .08s ease}
  .controls,.rankbar,.citybar{gap:8px}
  .controls button{padding:10px 14px;font-size:14px}
  .count{width:100%;margin:2px 0 0;text-align:left}
  .bar-label{width:100%;margin:2px 0}
  .rk,.ct,.tp,.chk{padding:9px 14px;font-size:14px}
  .filters-panel{padding:12px}
  .modal-back{padding:0;align-items:stretch}
  .modal{max-width:100%;min-height:100%;border-radius:0}
  .modal-inner{padding:16px 15px calc(28px + env(safe-area-inset-bottom))}
  .modal-close{top:calc(8px + env(safe-area-inset-top));width:38px;height:38px}
  .cost-box,.risk-box,.facts-box,.score-box{padding:14px}
  .score-list li{grid-template-columns:1fr 60px 28px;gap:8px}
  .detail-head{flex-direction:column;gap:8px}
  .gallery{grid-template-columns:1fr 1fr}
  .map-pane{height:42vh}
  .rf-card{padding:26px 20px calc(26px + env(safe-area-inset-bottom))}
}

/* =====================================================================
   DARK THEME — applied when <html> has class "dark". Redefines the core
   variables (so anything using them flips automatically) plus targeted
   overrides for the hard-coded light surfaces. Last in the cascade.
   ===================================================================== */
html.dark{
  --bg:#0f1115; --card:#171a21; --ink:#e8eaed; --muted:#98a1ad;
  --line:#2a2f39; --accent:#6aa4ff; --top:#22c55e; --new:#fb923c;
  --shadow:0 1px 3px rgba(0,0,0,.45),0 8px 24px rgba(0,0,0,.55);
  color-scheme:dark;
}
html.dark .site-header{background:#141821}
html.dark .card-media,html.dark .noimg,html.dark .score-bar{background:#1f2531}
html.dark .score-badge,html.dark .modal-close,html.dark .controls select,html.dark .controls button,
html.dark .rk,html.dark .ct,html.dark .tp,html.dark .filters-panel,html.dark .price-pill,
html.dark .facts-box,html.dark .score-box,html.dark .cost-box,html.dark .risk-box,
html.dark .hist-box,html.dark .note-box,html.dark .mk,html.dark .mk-btn,html.dark .vw,
html.dark .excluded-table{background:#171a21;color:var(--ink)}
html.dark .mk-save.on{background:#3a2f10;border-color:#f5b301;color:#f0c85a}
html.dark .mk-dismiss.on{background:#3a1c1c;border-color:#e05656;color:#f0a0a0}
html.dark .note-box textarea{background:#0f1115}
html.dark .spark{background:linear-gradient(180deg,transparent,rgba(255,255,255,.04))}
html.dark .chk{background:#20242e}
html.dark .chip{background:#222b3d;color:#bcc8e0}
html.dark .card-type{background:#241f38;color:#c4b5fd}
html.dark .cost-line{color:var(--ink)}
html.dark .reno-list,html.dark .risk-row ul,html.dark .risk-row li,html.dark .mk .i .t{color:#c4c9d2}
html.dark .pass-chip{background:#123021;color:#4ade80}
html.dark .src-link-card:hover,html.dark .langs .lang:hover:not(.active),
html.dark .site-header nav>a:hover{background:#20242e}
html.dark .card-price,html.dark .detail-price,html.dark .cost-big{color:var(--ink)}
html.dark .leaflet-tile{filter:brightness(.85) contrast(1.05)}
.theme-toggle{cursor:pointer;font-size:16px;line-height:1}
"""

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#ffffff">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Houses">
<title>House Leaderboard</title>
<script>try{var _t=localStorage.getItem('theme');if(_t==='dark'||(!_t&&window.matchMedia&&matchMedia('(prefers-color-scheme:dark)').matches)){document.documentElement.classList.add('dark');}}catch(e){}</script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>/*CSS*/</style>
</head>
<body>
<script id="data" type="application/octet-stream">__DATA_B64__</script>
<div id="app"></div>
<div class="modal-back" id="modalBack"><div class="modal"><div class="modal-inner">
  <button class="modal-close" id="modalClose">&times;</button>
  <div id="modalBody"></div>
</div></div></div>
<div class="modal-back" id="compareBack"><div class="modal cmp-modal"><div class="modal-inner">
  <button class="modal-close" id="compareClose">&times;</button>
  <div id="compareBody"></div>
</div></div></div>
<div class="compare-bar" id="compareBar"></div>
<div class="rf-overlay" id="rfOverlay"><div class="rf-card">
  <div class="rf-radar"><span>🏠</span></div>
  <div class="rf-title" id="rfTitle"></div>
  <div class="rf-status" id="rfStatus"></div>
  <div class="rf-steps" id="rfSteps"></div>
  <div class="rf-bar"><span></span></div>
  <div class="rf-wait" id="rfWait"></div>
  <button class="rf-close" id="rfClose">close</button>
</div></div>
<script>
(async () => {
const _raw = document.getElementById('data').textContent.trim();
let _json;
try{ const _b=Uint8Array.from(atob(_raw),c=>c.charCodeAt(0));
  _json=await new Response(new Blob([_b]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
}catch(e){ document.getElementById('app').innerHTML='<p style="padding:24px;font-family:sans-serif">Please open in a modern browser.</p>'; return; }
const DATA = JSON.parse(_json);
const EN = DATA.i18n.en;
let lang = localStorage.getItem('lang') || 'en'; if(!DATA.i18n[lang]) lang='en';
function t(k, vars){ let s=(DATA.i18n[lang]||{})[k]||EN[k]||k; if(vars) for(const p in vars) s=s.replace('{'+p+'}', vars[p]); return s; }
const esc = s => (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const euro = n => n==null?'':'€'+Number(n).toLocaleString('fi-FI',{maximumFractionDigits:0});
const euroK = n => n==null?'?':'€'+(n>=1000?Math.round(n/1000)+'k':Math.round(n));
const PARK = {garage:'garage',hall:'parking hall',covered:'carport',own_spot:'own spot',open_pole:'open + heating pole',open:'open spot',none:'no parking'};
const RISKCOL={low:'#0f9d58',medium:'#e8892b',high:'#e03131'};
function riskPill(labelKey, r){ if(!r) return ''; const c=RISKCOL[r.level]||'#888';
  return '<span class="risk" style="color:'+c+';border-color:'+c+'55;background:'+c+'14" title="'+esc((r.reasons||[]).join(' · '))+'">'+esc(t(labelKey))+': '+esc(t('risk_'+r.level))+'</span>'; }
function dropInfo(L){
  if(!L.prev_price||!L.price||L.prev_price<=L.price||!L.dropped_at) return null;
  if((Date.now()-Date.parse(L.dropped_at))/86400000 > 21) return null;   // only recent drops
  return {amt:L.prev_price-L.price, prev:L.prev_price, pct:Math.round((L.prev_price-L.price)/L.prev_price*100)};
}
// ---- data-trust + history helpers ----
function daysAgo(iso){ if(!iso) return null; return Math.max(0, Math.floor((Date.now()-Date.parse(iso))/86400000)); }
function timeAgo(iso){ if(!iso) return ''; const h=(Date.now()-Date.parse(iso))/3600000;
  if(h<1) return t('just_now'); if(h<24) return t('hours_ago',{n:Math.max(1,Math.round(h))});
  return t('days_ago',{n:Math.round(h/24)}); }
function sparkline(hist){
  const pts=(hist||[]).filter(p=>p&&p[1]!=null);
  if(pts.length<2) return '';
  const ys=pts.map(p=>p[1]), min=Math.min(...ys), max=Math.max(...ys), span=(max-min)||1;
  const W=260,H=46,pad=5;
  const xs=i=> pad + i*(W-2*pad)/(pts.length-1);
  const yv=v=> H-pad - (v-min)/span*(H-2*pad);
  const path=pts.map((p,i)=>(i?'L':'M')+xs(i).toFixed(1)+' '+yv(p[1]).toFixed(1)).join(' ');
  const first=pts[0][1], last=pts[pts.length-1][1];
  const col=last<first?'#0f9d58':(last>first?'#e03131':'#8a94a6');
  const dots=pts.map((p,i)=>'<circle cx="'+xs(i).toFixed(1)+'" cy="'+yv(p[1]).toFixed(1)+'" r="2.6" fill="'+col+'"/>').join('');
  return '<svg class="spark" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" role="img">'
    +'<path d="'+path+'" fill="none" stroke="'+col+'" stroke-width="2" vector-effect="non-scaling-stroke"/>'+dots+'</svg>';
}
function historySection(L){
  const hist=L.price_history||[], conf=L.confidence, dom=daysAgo(L.first_seen);
  let block='';
  const spark=sparkline(hist);
  if(spark){
    const first=hist[0][1], last=hist[hist.length-1][1], diff=last-first;
    const dtxt = diff===0 ? t('price_flat')
      : (diff<0?'↓ ':'↑ ')+euro(Math.abs(diff))+' '+t('since_listed');
    block='<div class="hist-block">'+spark+'<div class="hist-sub"><b>'+esc(dtxt)+'</b> · '
      +hist.length+' '+esc(t('data_points'))+'</div></div>';
  } else {
    block='<div class="hist-sub">'+esc(t('price_no_change'))+'</div>';
  }
  const rows=[];
  if(dom!=null) rows.push([t('days_on_market'), dom+' '+t('unit_days')]);
  if(L.first_seen) rows.push([t('first_listed'), new Date(L.first_seen).toLocaleDateString()]);
  if(L.last_seen) rows.push([t('last_checked'), timeAgo(L.last_seen)]);
  if(conf) rows.push([t('data_confidence'), conf.pct+'% · '+esc(t('conf_'+conf.level))]);
  const tbl='<table class="hist-tbl">'+rows.map(r=>'<tr><th>'+esc(r[0])+'</th><td>'+esc(r[1])+'</td></tr>').join('')+'</table>';
  const miss=(conf&&conf.missing&&conf.missing.length)
    ? '<div class="hist-missing">'+esc(t('not_verified'))+': '+conf.missing.map(esc).join(', ')+'</div>' : '';
  return '<section class="hist-box"><h2>'+esc(t('data_heading'))+'</h2>'+block+tbl+miss+'</section>';
}

// ---- state ----
let mapOn = localStorage.getItem('mapOn')==='1';
let rankBy = new Set((JSON.parse(localStorage.getItem('rankBy')||'null'))||['best']); if(!rankBy.size) rankBy=new Set(['best']);
const FILTER_KEYS = DATA.criteria.filter(c=>c.kind==='filter').map(c=>c.key);
let enabled = new Set(DATA.criteria.map(c=>c.key));   // all criteria on by default
const cap = s => s? s.charAt(0).toUpperCase()+s.slice(1) : s;
const ALL_CITIES = [...new Set(DATA.listings.map(L=>L.city).filter(Boolean))].sort();
let enabledCities = (()=>{ const s=JSON.parse(localStorage.getItem('cities')||'null');
  const set=new Set(s? s.filter(c=>ALL_CITIES.includes(c)) : ALL_CITIES); return set.size?set:new Set(ALL_CITIES); })();
const ALL_TYPES = [...new Set(DATA.listings.map(L=>L.type).filter(Boolean))].sort();
let enabledTypes = (()=>{ const s=JSON.parse(localStorage.getItem('types')||'null');
  const set=new Set(s? s.filter(x=>ALL_TYPES.includes(x)) : ALL_TYPES); return set.size?set:new Set(ALL_TYPES); })();

// ---- shortlist / triage marks (persisted per-device) ----
// Keyed by a STABLE property key (address+size+rooms) so a save survives a
// re-scrape even if the displayed source copy changes.
let marks = (()=>{ try{ return JSON.parse(localStorage.getItem('marks')||'{}'); }catch(e){ return {}; } })();
let markDirty=false;
let viewMode = localStorage.getItem('viewMode')||'all';   // all | saved | dismissed
function markKey(L){ return dedupKey(L)||L.id; }
function getMark(L){ return marks[markKey(L)]||{}; }
function setMark(L, patch){ const k=markKey(L); const m={...(marks[k]||{}),...patch};
  if(!m.save) delete m.save; if(!m.dismiss) delete m.dismiss; if(!m.note) delete m.note;
  if(Object.keys(m).length) marks[k]=m; else delete marks[k];
  try{ localStorage.setItem('marks', JSON.stringify(marks)); }catch(e){} }
function includedByMark(L){ const m=getMark(L);
  if(viewMode==='saved') return !!m.save;
  if(viewMode==='dismissed') return !!m.dismiss;
  return !m.dismiss; }   // 'all' view hides the ones you've rejected

// ---- compute ----
function bd(L){ return L.bedrooms!=null?L.bedrooms:(L.rooms!=null?L.rooms-1:null); }
function scoreOf(L){
  let num=0,den=0,prio=0,pw=0;
  for(const c of L.contribs){ if(!enabled.has(c.key)) continue;
    if(c.prio){ prio+=c.raw*c.weight; pw+=c.weight; } else { num+=c.raw*c.weight; den+=c.weight; } }
  return {score: den?Math.round(1000*num/den)/10:0, priority: pw?prio/pw:0};
}
function passesBase(L){   // everything except the shortlist/dismiss view filter
  if(enabledCities.size && !enabledCities.has(L.city)) return false;
  if(enabledTypes.size && L.type && !enabledTypes.has(L.type)) return false;
  for(const k of FILTER_KEYS){ if(enabled.has(k) && L.pass[k]===false) return false; }
  return true;
}
function included(L){ return includedByMark(L) && passesBase(L); }
function richness(L){ return ((L.features&&L.features.maintenance!=null)?2:0)+(L.reno_planned?1:0)+((L.features&&L.features.land_ownership)?1:0)+((L.price_history&&L.price_history.length>1)?1:0); }
function baseDedup(){   // one row per property, richest copy — no filters applied
  const best=new Map(), singles=[];
  for(const L of DATA.listings){ const k=dedupKey(L);
    if(!k){ singles.push(L); continue; }
    const cur=best.get(k); if(!cur || richness(L)>richness(cur)) best.set(k,L); }
  return [...best.values(), ...singles];
}
// rankable dimensions: value getter + whether higher is better
const DIMS = {
  best:{get:r=>r.score,hi:true}, price:{get:r=>r.L.price,hi:false}, ppm2:{get:r=>r.L.ppm2,hi:false},
  size:{get:r=>r.L.size,hi:true}, bedrooms:{get:r=>bd(r.L),hi:true}, transit:{get:r=>r.L.transit_min,hi:false},
  station:{get:r=>r.L.station_km,hi:false}, year:{get:r=>r.L.year,hi:true}, parking:{get:r=>r.L.parking_rank,hi:true},
  bank_safe:{get:r=>r.L.bank_risk?r.L.bank_risk.score:null,hi:false}, reno_safe:{get:r=>r.L.reno_risk?r.L.reno_risk.score:null,hi:false},
  cost5y:{get:r=>r.L.cost?r.L.cost.monthly:null,hi:false},
};
const RANK_ORDER=[['best','sort_best'],['price','sort_price'],['cost5y','sort_cost'],['ppm2','sort_ppm2'],['size','sort_size'],['bedrooms','sort_bedrooms'],['transit','sort_transit'],['station','sort_station'],['year','sort_year'],['parking','sort_parking'],['reno_safe','sort_reno'],['bank_safe','sort_bank']];
// Your commute destinations (from features.transit_minutes), in a stable order.
const COMMUTE_DESTS=(()=>{ const seen=[]; for(const L of DATA.listings){ const tm=L.features&&L.features.transit_minutes; if(tm) for(const k in tm) if(!seen.includes(k)) seen.push(k); } return seen; })();
function destLabel(n){ const k='dest_'+n, l=t(k); return l===k?n:l; }
function transitTo(L,n){ const tm=L.features&&L.features.transit_minutes; return tm&&tm[n]!=null?tm[n]:null; }
COMMUTE_DESTS.forEach(n=>{ DIMS['cm_'+n]={get:r=>transitTo(r.L,n),hi:false}; });
function dedupKey(L){ const a=(L.address||'').toLowerCase().replace(/[^a-z0-9äöå]/g,''); return a?a+'|'+L.size+'|'+L.rooms:null; }
function ranked(){
  // De-dup cross-posted copies FIRST (before any city/type/criteria filter), so a
  // property collapses to ONE canonical card with ONE type. Otherwise a home
  // cross-posted as e.g. omakotitalo AND erillistalo could keep showing under a
  // type you unchecked, via its other copy.
  let rows = DATA.listings.map(L=>{ const s=scoreOf(L); return {L, score:s.score, priority:s.priority}; });
  const rich = r => richness(r.L);
  const best=new Map(), singles=[];
  for(const r of rows){ const k=dedupKey(r.L);
    if(!k){ singles.push(r); continue; }
    const cur=best.get(k);
    if(!cur || rich(r)>rich(cur) || (rich(r)===rich(cur) && ((r.priority-cur.priority)||(r.score-cur.score))>0)) best.set(k,r);
  }
  rows=[...best.values(), ...singles].filter(r=>included(r.L));
  // Own plot ALWAYS beats a rented plot, in every ranking mode (own > unknown > rented).
  const landTier=r=>{ const v=r.L.features&&r.L.features.land_ownership; return v==='own'?2:(v==='rented'?0:1); };
  const dims=[...rankBy].filter(d=>DIMS[d]); if(!dims.length) dims.push('best');
  if(dims.length===1 && dims[0]==='best'){        // default: keep the own-land priority tier
    rows.sort((a,b)=>(landTier(b)-landTier(a))||(b.priority-a.priority)||(b.score-a.score));
  } else {                                        // additive combination of the chosen dimensions
    const range={};
    for(const d of dims){ const vs=rows.map(r=>DIMS[d].get(r)).filter(v=>v!=null&&!isNaN(v)); range[d]=[Math.min(...vs),Math.max(...vs)]; }
    rows.forEach(r=>{ let sum=0;
      for(const d of dims){ const v=DIMS[d].get(r); let nv=0;
        if(v!=null&&!isNaN(v)){ const a=range[d][0],b=range[d][1]; nv=(b===a)?1:(v-a)/(b-a); if(!DIMS[d].hi) nv=1-nv; }
        sum+=nv; }
      r.combo=sum/dims.length; });
    rows.sort((a,b)=>(landTier(b)-landTier(a))||(b.combo-a.combo)||(b.score-a.score));
  }
  rows.forEach((r,i)=>r.rank=i+1);
  return rows;
}

// ---- cards / facts ----
function facts(L){
  const f=L.features||{}; const rows=[]; const add=(k,v)=>{ if(v!=null&&v!=='') rows.push('<tr><th>'+esc(t(k))+'</th><td>'+esc(v)+'</td></tr>'); };
  add('fact_size', L.size!=null?L.size+' m²':null); add('fact_rooms', L.rooms);
  add('fact_bedrooms', bd(L)); add('fact_type', L.type); add('fact_year', L.year); add('fact_floor', L.floor);
  if(f.duplex!=null) add('fact_duplex', f.duplex?t('yes'):t('no'));
  if(f.toilets!=null) add('fact_toilets', f.toilets);
  if(f.sauna) add('fact_sauna', f.sauna.private?t('sauna_private'):t('sauna_shared'));
  if(f.balcony&&f.balcony.present) add('fact_balcony', f.balcony.glazed?t('glazed'):t('unglazed'));
  if(f.parking) add('fact_parking', PARK[f.parking.type]||f.parking.type);
  if(f.land_ownership) add('fact_land', t('land_'+f.land_ownership));
  if(f.energy_class) add('fact_energy', f.energy_class);
  for(const n of COMMUTE_DESTS){ const mi=transitTo(L,n); if(mi!=null)
    rows.push('<tr><th>🚆 '+esc(destLabel(n))+'</th><td>'+mi+' '+esc(t('unit_min'))+' '+esc(t('by_transit'))+'</td></tr>'); }
  return '<table>'+rows.join('')+'</table>';
}
function card(r){
  const L=r.L, top=r.rank<=DATA.top_n, d=dropInfo(L);
  const img=L.photos&&L.photos[0]?'<img loading="lazy" src="'+esc(L.photos[0])+'" alt="">':'<div class="noimg">—</div>';
  const chips=L.contribs.filter(c=>enabled.has(c.key)).map(c=>'<span class="chip">'+esc(c.label)+'</span>').join('');
  const fa=[L.size!=null?L.size+' m²':'', L.rooms!=null?L.rooms+' '+t('unit_rooms'):'', L.year||'', L.district||L.city].filter(Boolean).map(x=>'<span>'+esc(x)+'</span>').join('');
  const sel=compareSet.has(L.id), m=getMark(L);
  const cls=['card',top?'card-top':'',sel?'cmp-on':'',m.save?'saved':'',m.dismiss?'dismissed':''].filter(Boolean).join(' ');
  return '<article class="'+cls+'" data-id="'+esc(L.id)+'">'
    +'<div class="card-media">'+img+'<span class="rank-badge">#'+r.rank+'</span><span class="score-badge">'+r.score+'</span>'+(L.new?'<span class="new-badge">'+esc(t('badge_new'))+'</span>':'')+(d?'<span class="drop-badge">▼ '+euroK(d.amt)+'</span>':'')+'<button class="cmp-btn'+(sel?' on':'')+'" data-cmp="'+esc(L.id)+'" title="'+esc(t('compare_add'))+'">'+(sel?'✓':'+')+'</button></div>'
    +'<div class="card-body">'+(L.type?'<div class="card-type">'+esc(cap(L.type))+'</div>':'')+'<div class="card-title">'+esc(L.title)+'</div>'
    +'<div class="card-price">'+euro(L.price)+(d?' <span class="drop-was">↓ '+euro(d.amt)+' · '+esc(t('was'))+' '+euro(d.prev)+'</span>':'')+(L.ppm2?' <span class="ppm2'+(L.area_ppm2&&L.ppm2>1.15*L.area_ppm2?' ppm2-high':'')+'">· '+euro(L.ppm2)+'/m²</span>':'')+'</div>'
    +(L.area_ppm2?'<div class="area-line" title="'+esc(t('area_price_tip'))+'">'+esc(t('area_price'))+' ≈ '+euro(L.area_ppm2)+'/m²</div>':'')
    +(L.cost?'<div class="cost-line" title="'+esc(t('cost_heading'))+'">≈ '+euro(L.cost.monthly)+' / '+esc(t('per_month'))+' <span class="cost-sub">'+esc(t('cost_tag'))+(L.cost.charges_estimated?' *':'')+'</span></div>':'')
    +'<div class="card-facts">'+fa+'</div>'+commuteLine(L)+'<div class="card-chips">'+chips+'</div>'
    +'<div class="risks">'+riskPill('bank_label',L.bank_risk)+riskPill('reno_label',L.reno_risk)+'</div>'
    +trustLine(L)
    +'<div class="mark-actions">'
      +'<button class="mk-btn mk-save'+(m.save?' on':'')+'" data-mk="save">'+(m.save?'★ '+esc(t('saved')):'☆ '+esc(t('save')))+'</button>'
      +'<button class="mk-btn mk-dismiss'+(m.dismiss?' on':'')+'" data-mk="dismiss">'+(m.dismiss?esc(t('undo')):'🗑 '+esc(t('dismiss')))+'</button>'
      +(m.note?'<span class="mk-note-flag" title="'+esc(m.note)+'">📝</span>':'')
    +'</div>'
    +'<a class="src-link-card" href="'+esc(L.url)+'" target="_blank" rel="noopener" onclick="event.stopPropagation()">'+esc(t('view_on',{source:L.source}))+'</a></div></article>';
}
function commuteLine(L){
  if(!COMMUTE_DESTS.length) return '';
  const parts=COMMUTE_DESTS.map(n=>{ const m=transitTo(L,n); return m==null?null:'<span>'+esc(destLabel(n))+' '+m+'m</span>'; }).filter(Boolean);
  return parts.length?'<div class="commute-line">🚆 '+parts.join(' · ')+'</div>':'';
}
function trustLine(L){
  const dom=daysAgo(L.first_seen), c=L.confidence; if(dom==null&&!c) return '';
  const days = dom!=null ? '<span title="'+esc(t('days_on_market'))+'">🕒 '+dom+esc(t('unit_days_short'))+'</span>' : '';
  const conf = c ? '<span class="conf conf-'+c.level+'" title="'+esc(t('data_confidence'))+' — '+esc(c.missing&&c.missing.length?t('not_verified')+': '+c.missing.join(', '):t('all_known'))+'"><i class="dot"></i>'+c.pct+'%</span>' : '';
  return '<div class="trust">'+days+conf+'</div>';
}
function wireCards(root){
  root.querySelectorAll('.card').forEach(c=>c.onclick=()=>openModal(c.dataset.id));
  root.querySelectorAll('.cmp-btn').forEach(b=>b.onclick=e=>{ e.stopPropagation(); toggleCompare(b.dataset.cmp); });
  root.querySelectorAll('.mk-btn').forEach(b=>b.onclick=e=>{ e.stopPropagation();
    const L=DATA.listings.find(x=>x.id===b.closest('.card').dataset.id); if(!L) return; const cur=getMark(L);
    if(b.dataset.mk==='save') setMark(L,{save:!cur.save, dismiss:false});
    else setMark(L,{dismiss:!cur.dismiss, save:false});
    renderContent(); renderViewBar(); });
}
// ---- compare tray + side-by-side ----
let compareSet = new Set((()=>{ try{ return JSON.parse(localStorage.getItem('compare')||'[]'); }catch(e){ return []; } })());
const CMP_MAX = 4;
const CMP_ROWS = [
  {label:()=>t('sort_price'), get:L=>L.price, fmt:v=>euro(v), better:'lo'},
  {label:()=>'€/m²', get:L=>L.ppm2, fmt:v=>euro(v)+'/m²', better:'lo'},
  {label:()=>t('area_price'), get:L=>L.area_ppm2, fmt:v=>euro(v)+'/m²', better:null},
  {label:()=>t('fact_size'), get:L=>L.size, fmt:v=>v+' m²', better:'hi'},
  {label:()=>t('fact_rooms'), get:L=>L.rooms, fmt:v=>''+v, better:'hi'},
  {label:()=>t('fact_bedrooms'), get:L=>bd(L), fmt:v=>''+v, better:'hi'},
  {label:()=>t('fact_year'), get:L=>L.year, fmt:v=>''+v, better:'hi'},
  {label:()=>t('fact_type'), get:L=>L.type?cap(L.type):null, fmt:v=>v, better:null},
  {label:()=>t('cost_tag'), get:L=>L.cost?L.cost.monthly:null, fmt:v=>euro(v)+' / '+t('per_month'), better:'lo'},
  {label:()=>t('bank_label'), get:L=>L.bank_risk?L.bank_risk.score:null, fmt:(v,L)=>t('risk_'+L.bank_risk.level), better:'lo'},
  {label:()=>t('reno_label'), get:L=>L.reno_risk?L.reno_risk.score:null, fmt:(v,L)=>t('risk_'+L.reno_risk.level), better:'lo'},
  {label:()=>t('sort_transit'), get:L=>L.transit_min, fmt:v=>v+' min', better:'lo'},
  {label:()=>t('sort_station'), get:L=>L.station_km, fmt:v=>v+' km', better:'lo'},
  {label:()=>t('fact_parking'), get:L=>L.parking_rank, fmt:(v,L)=>{const p=L.features&&L.features.parking;return p?(PARK[p.type]||p.type):'—';}, better:'hi'},
  {label:()=>t('fact_land'), get:L=>(L.features&&L.features.land_ownership)||null, fmt:v=>t('land_'+v), better:null},
  {label:()=>t('fact_energy'), get:L=>(L.features&&L.features.energy_class)||null, fmt:v=>v, better:null},
  {label:()=>t('days_on_market'), get:L=>daysAgo(L.first_seen), fmt:v=>v+' '+t('unit_days'), better:null},
  {label:()=>t('data_confidence'), get:L=>L.confidence?L.confidence.pct:null, fmt:v=>v+'%', better:'hi'},
];
function toggleCompare(id){
  if(compareSet.has(id)) compareSet.delete(id);
  else { if(compareSet.size>=CMP_MAX) return; compareSet.add(id); }
  try{ localStorage.setItem('compare', JSON.stringify([...compareSet])); }catch(e){}
  document.querySelectorAll('.cmp-btn').forEach(b=>{ if(b.dataset.cmp===id){ b.classList.toggle('on',compareSet.has(id)); b.textContent=compareSet.has(id)?'✓':'+'; } });
  document.querySelectorAll('.card').forEach(c=>{ if(c.dataset.id===id) c.classList.toggle('cmp-on',compareSet.has(id)); });
  renderCompareBar();
}
function clearCompare(){ compareSet.clear(); try{localStorage.setItem('compare','[]');}catch(e){}
  document.querySelectorAll('.cmp-btn.on').forEach(b=>{ b.classList.remove('on'); b.textContent='+'; });
  document.querySelectorAll('.card.cmp-on').forEach(c=>c.classList.remove('cmp-on'));
  renderCompareBar(); }
function renderCompareBar(){
  const bar=document.getElementById('compareBar'); if(!bar) return;
  const Ls=[...compareSet].map(id=>DATA.listings.find(x=>x.id===id)).filter(Boolean);
  if(!Ls.length){ bar.classList.remove('show'); bar.innerHTML=''; return; }
  const thumbs=Ls.map(L=>L.photos&&L.photos[0]?'<img src="'+esc(L.photos[0])+'" alt="">':'').join('');
  bar.innerHTML='<span class="cb-label">'+esc(t('compare_count',{n:Ls.length}))+'</span><span class="cb-thumbs">'+thumbs+'</span>'
    +'<button class="cb-go" id="cbGo"'+(Ls.length<2?' disabled':'')+'>'+esc(t('compare_go'))+'</button>'
    +'<button class="cb-clear" id="cbClear">'+esc(t('clear'))+'</button>';
  bar.classList.add('show');
  document.getElementById('cbGo').onclick=openCompare;
  document.getElementById('cbClear').onclick=clearCompare;
}
function openCompare(){
  const Ls=[...compareSet].map(id=>DATA.listings.find(x=>x.id===id)).filter(Boolean);
  if(Ls.length<2) return;
  let head='<tr><th class="cmp-metric"></th>'+Ls.map(L=>'<th class="cmp-col-head">'
    +(L.photos&&L.photos[0]?'<img src="'+esc(L.photos[0])+'" alt="">':'')
    +'<div class="ch-title">'+esc(L.title)+'</div>'
    +'<a href="'+esc(L.url)+'" target="_blank" rel="noopener" class="ch-link">'+esc(t('view_on',{source:L.source}))+'</a>'
    +'<button class="ch-rm" data-rm="'+esc(L.id)+'">✕ '+esc(t('remove'))+'</button></th>').join('')+'</tr>';
  let body='';
  for(const row of CMP_ROWS){
    const vals=Ls.map(L=>row.get(L));
    const best=new Set();
    if(row.better){ const nums=vals.filter(v=>typeof v==='number');
      if(nums.length>=2 && new Set(nums).size>1){ const b=row.better==='hi'?Math.max(...nums):Math.min(...nums);
        vals.forEach((v,i)=>{ if(v===b) best.add(i); }); } }
    body+='<tr><td class="cmp-metric">'+esc(row.label().replace(/[↑↓]/g,'').trim())+'</td>'+Ls.map((L,i)=>{
      const v=vals[i]; const disp=(v==null||v==='')?'—':row.fmt(v,L);
      return '<td class="'+(best.has(i)?'cmp-best':'')+'">'+esc(String(disp))+'</td>'; }).join('')+'</tr>';
  }
  document.getElementById('compareBody').innerHTML='<h1 style="font-size:22px;margin:0 0 4px">'+esc(t('compare_heading'))+'</h1>'
    +'<p style="color:var(--muted);margin:0 0 16px;font-size:13px">'+esc(t('compare_hint'))+'</p>'
    +'<div class="cmp-wrap"><table class="cmp-table">'+head+body+'</table></div>';
  document.getElementById('compareBack').classList.add('open');
  document.querySelectorAll('#compareBody .ch-rm').forEach(b=>b.onclick=()=>{ toggleCompare(b.dataset.rm); if(compareSet.size<2){ closeCompare(); } else openCompare(); });
}
function closeCompare(){ document.getElementById('compareBack').classList.remove('open'); }
function riskSection(L){
  const row=(labelKey,r)=>{ if(!r) return ''; const c=RISKCOL[r.level];
    return '<div class="risk-row"><div class="risk-h" style="color:'+c+'">'+esc(t(labelKey))+': '+esc(t('risk_'+r.level))+'</div><ul>'+(r.reasons||[]).map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></div>'; };
  if(!L.bank_risk && !L.reno_risk) return '';
  return '<section class="risk-box"><h2>'+esc(t('risk_heading'))+'</h2>'+row('bank_label',L.bank_risk)+row('reno_label',L.reno_risk)
    +'<div class="risk-disc">'+esc(t('risk_disclaimer'))+'</div></section>';
}
function costSection(L){
  if(!L.cost) return '';
  const c=L.cost, b=c.breakdown;
  const row=(k,v)=>'<tr><th>'+esc(t(k))+'</th><td>'+euro(v)+' / '+esc(t('per_month'))+'</td></tr>';
  let reno='';
  if(L.reno_planned) reno+='<div class="reno-list"><b>'+esc(t('reno_planned_label'))+':</b> '+esc(L.reno_planned)+'</div>';
  if(L.reno_done) reno+='<div class="reno-list"><b>'+esc(t('reno_done_label'))+':</b> '+esc(L.reno_done)+'</div>';
  const fin = b.financing ? row('cost_financing', b.financing) : '';
  return '<section class="cost-box"><h2>'+esc(t('cost_heading'))+'</h2>'
    +'<div class="cost-big">≈ '+euro(c.monthly)+' / '+esc(t('per_month'))+'</div>'
    +'<table>'+row('cost_charges',b.maintenance)+fin+row('cost_renovation',b.renovation)+'</table>'
    +reno
    +'<div class="risk-disc">'+esc(t('cost_note'))+(c.charges_estimated?' '+esc(t('cost_charges_est')):'')+'</div></section>';
}

// ---- render shell ----
function render(){
  document.documentElement.lang=lang;
  const langs=Object.keys(DATA.langs).map(c=>'<a href="#" class="lang '+(c===lang?'active':'')+'" data-lang="'+c+'">'+DATA.langs[c]+'</a>').join('');
  const rankChips=RANK_ORDER.map(o=>'<span class="rk '+(rankBy.has(o[0])?'on':'')+'" data-rk="'+o[0]+'">'+esc(t(o[1]))+'</span>').join('')
    +COMMUTE_DESTS.map(n=>'<span class="rk '+(rankBy.has('cm_'+n)?'on':'')+'" data-rk="cm_'+n+'" title="'+esc(t('sort_commute'))+'">⏱ '+esc(destLabel(n))+'</span>').join('');
  const cityChips=ALL_CITIES.map(c=>'<span class="ct '+(enabledCities.has(c)?'on':'')+'" data-ct="'+esc(c)+'">'+esc(c)+'</span>').join('');
  const typeChips=ALL_TYPES.map(x=>'<span class="tp '+(enabledTypes.has(x)?'on':'')+'" data-tp="'+esc(x)+'">'+esc(cap(x))+'</span>').join('');
  document.getElementById('app').innerHTML=
    '<header class="site-header"><a class="brand" href="#">🏠 '+esc(DATA.title)+'</a>'
    +'<nav><a href="#" id="mapToggle" class="'+(mapOn?'active':'')+'">🗺 '+esc(t('map_view'))+'</a>'
    +'<a href="#" id="themeToggle" class="theme-toggle" title="'+esc(t('theme_toggle'))+'" aria-label="'+esc(t('theme_toggle'))+'">'+(document.documentElement.classList.contains('dark')?'☀️':'🌙')+'</a>'
    +'<span class="langs">'+langs+'</span></nav></header>'
    +'<main><div class="board-intro"><h1 id="headline"></h1>'
    +'<p>'+esc(t('index_sub',{top_n:DATA.top_n}))+' <span class="stamp">· '+esc(DATA.generated)+'</span></p></div>'
    +'<div class="controls"><button id="filtersBtn">⚙ '+esc(t('filters'))+'</button>'
    +(DATA.dispatch?'<button id="refreshBtn">🔄 '+esc(t('rf_button'))+'</button>':'')
    +'<span class="count" id="count"></span></div>'
    +'<div class="viewbar" id="viewBar"></div>'
    +'<div class="rankbar"><span class="bar-label">'+esc(t('sort_by'))+'</span>'+rankChips+'</div>'
    +(ALL_TYPES.length>1?'<div class="citybar"><span class="bar-label">'+esc(t('types_label'))+'</span>'+typeChips+'</div>':'')
    +(ALL_CITIES.length>1?'<div class="citybar"><span class="bar-label">'+esc(t('cities_label'))+'</span>'+cityChips+'</div>':'')
    +'<div class="filters-panel" id="filtersPanel"></div>'
    +'<div id="content"></div></main>';
  buildFiltersPanel();
  document.querySelectorAll('.lang').forEach(a=>a.onclick=e=>{e.preventDefault();lang=a.dataset.lang;localStorage.setItem('lang',lang);render();});
  document.querySelectorAll('.rk').forEach(el=>el.onclick=()=>{ const k=el.dataset.rk;
    if(rankBy.has(k)){ if(rankBy.size>1) rankBy.delete(k); } else rankBy.add(k);
    localStorage.setItem('rankBy',JSON.stringify([...rankBy])); el.classList.toggle('on',rankBy.has(k)); renderContent(); });
  document.querySelectorAll('.ct').forEach(el=>el.onclick=()=>{ const c=el.dataset.ct;
    if(enabledCities.has(c)){ if(enabledCities.size>1) enabledCities.delete(c); } else enabledCities.add(c);
    localStorage.setItem('cities',JSON.stringify([...enabledCities])); el.classList.toggle('on',enabledCities.has(c)); renderContent(); });
  document.querySelectorAll('.tp').forEach(el=>el.onclick=()=>{ const x=el.dataset.tp;
    if(enabledTypes.has(x)){ if(enabledTypes.size>1) enabledTypes.delete(x); } else enabledTypes.add(x);
    localStorage.setItem('types',JSON.stringify([...enabledTypes])); el.classList.toggle('on',enabledTypes.has(x)); renderContent(); });
  document.getElementById('filtersBtn').onclick=()=>document.getElementById('filtersPanel').classList.toggle('open');
  document.getElementById('themeToggle').onclick=e=>{e.preventDefault();
    const dark=document.documentElement.classList.toggle('dark');
    localStorage.setItem('theme',dark?'dark':'light');
    e.currentTarget.textContent=dark?'☀️':'🌙';
    const tc=document.querySelector('meta[name=theme-color]'); if(tc) tc.setAttribute('content',dark?'#0f1115':'#ffffff'); };
  document.getElementById('mapToggle').onclick=e=>{e.preventDefault();mapOn=!mapOn;localStorage.setItem('mapOn',mapOn?'1':'0');render();};
  if(DATA.dispatch){ const rb=document.getElementById('refreshBtn'); if(rb) rb.onclick=doRefresh; }
  renderViewBar();
  renderContent();
}
// ---- shortlist view bar (All / ⭐ Shortlist / 🗑 Dismissed) ----
function markCounts(){ let all=0, saved=0, dismissed=0;
  for(const L of baseDedup()){ if(!passesBase(L)) continue; const m=getMark(L);
    if(m.dismiss) dismissed++; else all++; if(m.save) saved++; } return {all,saved,dismissed}; }
function renderViewBar(){ const bar=document.getElementById('viewBar'); if(!bar) return;
  const c=markCounts();
  const item=(v,label,n)=>'<span class="vw '+(viewMode===v?'on':'')+'" data-vw="'+v+'">'+esc(label)+' <b>'+n+'</b></span>';
  bar.innerHTML=item('all',t('view_all'),c.all)+item('saved','⭐ '+t('view_saved'),c.saved)+item('dismissed','🗑 '+t('view_dismissed'),c.dismissed);
  bar.querySelectorAll('.vw').forEach(el=>el.onclick=()=>{ viewMode=el.dataset.vw; localStorage.setItem('viewMode',viewMode);
    renderViewBar(); renderContent(); }); }

// ---- manual refresh (trigger the GitHub Action) ----
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function rfEl(id){ return document.getElementById(id); }
function rfSteps(active){ const steps=[['q','rf_step_q'],['f','rf_step_f'],['r','rf_step_r'],['p','rf_step_p']];
  rfEl('rfSteps').innerHTML=steps.map((s,i)=>'<span class="s '+(i<active?'done':(i===active?'active':''))+'">'+(i<active?'✓ ':'')+esc(t(s[1]))+'</span>').join(''); }
async function ghApi(path,opts){ return fetch('https://api.github.com'+path,{...(opts||{}),
  headers:{'Authorization':'Bearer '+DATA.dispatch.token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}}); }
let rfBusy=false, rfCancelled=false;
async function doRefresh(){
  if(rfBusy) return; rfBusy=true; rfCancelled=false;
  const D=DATA.dispatch;
  rfEl('rfOverlay').classList.add('open'); rfEl('rfTitle').textContent=t('rf_title'); rfEl('rfWait').textContent=t('rf_wait');
  const st=rfEl('rfStatus'); st.classList.remove('rf-err'); const t0=Date.now(); rfSteps(0); st.textContent=t('rf_queued');
  const stop=()=>{ rfBusy=false; };
  try{
    const disp=await ghApi('/repos/'+D.repo+'/actions/workflows/'+D.workflow+'/dispatches',{method:'POST',body:JSON.stringify({ref:D.ref})});
    if(disp.status!==204){ await disp.text(); throw new Error([401,403,404].includes(disp.status)?'token not authorized for this repo':('GitHub error '+disp.status)); }
    let runId=null;
    for(let i=0;i<24&&runId===null;i++){ await sleep(3000); if(rfCancelled) return stop();
      const j=await (await ghApi('/repos/'+D.repo+'/actions/workflows/'+D.workflow+'/runs?event=workflow_dispatch&per_page=5')).json();
      const c=(j.workflow_runs||[]).find(w=>new Date(w.created_at).getTime()>=t0-20000); if(c) runId=c.id; }
    if(runId===null) throw new Error('run not found');
    let done=false;
    for(let i=0;i<95&&!done;i++){ await sleep(4000); if(rfCancelled) return stop();
      const run=await (await ghApi('/repos/'+D.repo+'/actions/runs/'+runId)).json();
      if(run.status==='queued'){ rfSteps(0); st.textContent=t('rf_queued'); }
      else if(run.status==='in_progress'){ const s=((Date.now()-t0)/1000)<50?1:2; rfSteps(s); st.textContent=t(s===1?'rf_fetching':'rf_ranking'); }
      else if(run.status==='completed'){ done=true; if(run.conclusion!=='success') throw new Error('run '+run.conclusion); }
    }
    if(!done) throw new Error('timed out');
    rfSteps(3); st.textContent=t('rf_publishing'); await sleep(25000); if(rfCancelled) return stop();
    rfSteps(4); st.textContent=t('rf_done'); await sleep(1200); if(rfCancelled) return stop();
    location.reload();
  }catch(e){ st.classList.add('rf-err'); st.textContent=t('rf_error',{e:(e&&e.message)||e}); rfBusy=false; }
}
function buildFiltersPanel(){
  const p=document.getElementById('filtersPanel');
  const item=c=>'<label class="chk '+(enabled.has(c.key)?'':'off')+'"><input type="checkbox" data-key="'+c.key+'"'+(enabled.has(c.key)?' checked':'')+'>'+esc(c.title)+'</label>';
  p.innerHTML='<div class="grp">'+esc(t('req_group'))+'</div>'+DATA.criteria.filter(c=>c.kind==='filter'&&c.key!=='property_type').map(item).join('')
    +'<div class="grp">'+esc(t('pref_group'))+'</div>'+DATA.criteria.filter(c=>c.kind==='score').map(item).join('');
  p.querySelectorAll('input').forEach(i=>i.onchange=()=>{ i.checked?enabled.add(i.dataset.key):enabled.delete(i.dataset.key);
    i.closest('.chk').classList.toggle('off',!i.checked); renderContent(); });
}
function setHeadCount(n){
  const h=document.getElementById('headline'); if(h) h.textContent=t('index_heading',{n});
  const c=document.getElementById('count'); if(c) c.textContent='';
}
function renderContent(){ if(mapOn) renderMap(); else renderGrid(); }

function renderGrid(){
  const rows=ranked(); setHeadCount(rows.length);
  document.getElementById('content').innerHTML='<div class="grid" id="grid">'+rows.slice(0,400).map(card).join('')+'</div>'
    +(rows.length>400?'<p class="excluded-hint">'+esc(t('showing_first',{n:400}))+'</p>':'');
  wireCards(document.getElementById('grid'));
}

// ---- map view ----
let map=null, layer=null, allRows=[];
function renderMap(){
  allRows=ranked(); setHeadCount(allRows.length);
  document.getElementById('content').innerHTML='<div class="split"><div class="map-pane"><div id="bigmap"></div></div>'
    +'<div class="list-pane"><div class="count" id="viscount" style="margin:0 0 10px"></div><div class="grid" id="mlist"></div></div></div>';
  if(map){map.remove();map=null;}
  map=L.map('bigmap',{scrollWheelZoom:true}).setView([60.19,24.94],11); window._leafmap=map;
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
  layer=L.layerGroup().addTo(map);
  const pts=[];
  allRows.slice(0,400).forEach(r=>{ const L2=r.L; if(L2.lat==null||L2.lon==null) return; pts.push([L2.lat,L2.lon]);
    const icon=L.divIcon({className:'',html:'<div class="price-pill '+(r.rank<=DATA.top_n?'top':'')+'">'+euroK(L2.price)+'</div>'});
    const m=L.marker([L2.lat,L2.lon],{icon}).addTo(layer);
    m.bindPopup(popupHtml(r),{maxWidth:280,minWidth:270});
    m.on('popupopen',ev=>{ const b=ev.popup.getElement().querySelector('.mk button'); if(b) b.onclick=()=>openModal(L2.id); });
  });
  map.on('moveend',updateMapList);
  setTimeout(()=>{ map.invalidateSize();
    if(pts.length) map.fitBounds(pts,{padding:[40,40],maxZoom:14,animate:false});
    updateMapList(); },60);
}
function popupHtml(r){ const L2=r.L;
  return '<div class="mk">'+(L2.photos&&L2.photos[0]?'<img src="'+esc(L2.photos[0])+'">':'')
    +'<div class="i"><div class="p">'+euro(L2.price)+'</div><div class="t">'+esc(L2.title)+'</div>'
    +'<div class="f">#'+r.rank+' · '+(L2.size?L2.size+' m² · ':'')+(L2.rooms||'')+' '+esc(t('unit_rooms'))+'</div>'
    +'<button>'+esc(t('key_facts'))+' →</button></div></div>';
}
function updateMapList(){
  if(!map) return; const b=map.getBounds();
  const vis=allRows.filter(r=>r.L.lat!=null&&b.contains([r.L.lat,r.L.lon]));
  const ml=document.getElementById('mlist'); if(!ml) return;
  ml.innerHTML=vis.slice(0,200).map(card).join(''); wireCards(ml);
  const vc=document.getElementById('viscount'); if(vc) vc.textContent=t('in_view',{n:vis.length,total:allRows.length});
}

// ---- modal ----
function openModal(id){
  const L=DATA.listings.find(x=>x.id===id); if(!L) return; const s=scoreOf(L), d=dropInfo(L);
  const gallery=(L.photos||[]).map(p=>'<img loading="lazy" src="'+esc(p)+'">').join('');
  const bars=L.contribs.map(c=>'<li><span class="score-label">'+esc(c.label)+'</span><span class="score-bar"><span style="width:'+Math.round(c.raw*100)+'%"></span></span><span class="score-w">×'+c.weight+'</span></li>').join('');
  const plans=(L.floor_plans||[]).map(p=>'<img loading="lazy" src="'+esc(p)+'">').join('');
  document.getElementById('modalBody').innerHTML=
    '<div class="detail-rank">'+esc(t('rank_score',{rank:'—',score:s.score}))+'</div>'
    +'<h1>'+esc(L.title)+'</h1><div class="detail-addr">'+esc([L.address,L.district,L.city].filter(Boolean).join(', '))+'</div>'
    +'<div class="detail-price">'+euro(L.price)+(d?' <span class="drop-was">↓ '+euro(d.amt)+' · '+esc(t('was'))+' '+euro(d.prev)+'</span>':'')+(L.ppm2?' <span class="ppm2">'+euro(L.ppm2)+'/m²</span>':'')+'</div>'
    +'<a class="src-link" href="'+esc(L.url)+'" target="_blank" rel="noopener">'+esc(t('view_on',{source:L.source}))+'</a>'
    +'<div class="detail-marks"><button class="mk-btn mk-save" id="mSave"></button><button class="mk-btn mk-dismiss" id="mDismiss"></button></div>'
    +(gallery?'<div class="gallery" style="margin-top:16px">'+gallery+'</div>':'')
    +'<div class="detail-cols"><section class="facts-box"><h2>'+esc(t('key_facts'))+'</h2>'+facts(L)+'</section>'
    +'<section class="score-box"><h2>'+esc(t('why_rank'))+'</h2><ul class="score-list">'+bars+'</ul></section></div>'
    +'<section class="note-box"><h2>📝 '+esc(t('notes'))+'</h2><textarea id="noteArea" placeholder="'+esc(t('note_ph'))+'">'+esc(getMark(L).note||'')+'</textarea></section>'
    +costSection(L)
    +riskSection(L)
    +historySection(L)
    +(plans?'<section><h2>'+esc(t('floor_plan'))+'</h2><div class="gallery">'+plans+'</div></section>':'')
    +(L.lat&&L.lon?'<section><h2>'+esc(t('location'))+'</h2><div id="dmap" style="height:320px;border-radius:12px"></div></section>':'');
  const sv=document.getElementById('mSave'), ds=document.getElementById('mDismiss'), na=document.getElementById('noteArea');
  const paint=()=>{ const m=getMark(L);
    sv.className='mk-btn mk-save'+(m.save?' on':''); sv.textContent=m.save?'★ '+t('saved'):'☆ '+t('save');
    ds.className='mk-btn mk-dismiss'+(m.dismiss?' on':''); ds.textContent=m.dismiss?t('undo'):'🗑 '+t('dismiss'); };
  paint();
  sv.onclick=()=>{ const c=getMark(L); setMark(L,{save:!c.save,dismiss:false}); paint(); renderContent(); renderViewBar(); };
  ds.onclick=()=>{ const c=getMark(L); setMark(L,{dismiss:!c.dismiss,save:false}); paint(); renderContent(); renderViewBar(); };
  na.oninput=()=>{ setMark(L,{note:na.value}); markDirty=true; };
  document.getElementById('modalBack').classList.add('open');
  if(L.lat&&L.lon){ if(window._dm){window._dm.remove();} window._dm=L2map(L); }
}
function L2map(L){ const m=window.L.map('dmap').setView([L.lat,L.lon],14);
  window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(m);
  window.L.marker([L.lat,L.lon]).addTo(m); setTimeout(()=>m.invalidateSize(),50); return m; }
function closeModal(){ document.getElementById('modalBack').classList.remove('open'); if(window._dm){window._dm.remove();window._dm=null;}
  if(markDirty){ markDirty=false; renderContent(); renderViewBar(); } }
document.getElementById('modalClose').onclick=closeModal;
document.getElementById('modalBack').onclick=e=>{ if(e.target.id==='modalBack') closeModal(); };
document.getElementById('compareClose').onclick=closeCompare;
document.getElementById('compareBack').onclick=e=>{ if(e.target.id==='compareBack') closeCompare(); };
document.getElementById('rfClose').onclick=()=>{ rfCancelled=true; rfBusy=false; document.getElementById('rfOverlay').classList.remove('open'); };
render();
renderCompareBar();
})();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
