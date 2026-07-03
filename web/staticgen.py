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

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core.criteria import build_criteria
from core.criteria.builtins import TransitProximity, haversine_km
from core.i18n import LANG_NAMES, TRANSLATIONS

BASE = Path(__file__).parent

_PARK_RANK = {"garage": 6, "hall": 5, "covered": 4, "own_spot": 3,
              "open_pole": 2, "open": 1, "none": 0}


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


def _candidate_payload(L, filters, scorers, prev) -> dict:
    tm = L.features.get("transit_minutes") or {}
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
    return {
        "id": L.uid, "source": L.source, "url": L.url, "title": L.title,
        "price": L.price, "ppm2": L.price_per_m2, "size": L.size_m2,
        "rooms": L.rooms, "bedrooms": L.bedrooms, "year": L.year_built,
        "floor": L.floor, "type": L.property_type,
        "address": L.address, "district": L.district, "city": L.city,
        "lat": L.lat, "lon": L.lon,
        "photos": L.photos, "floor_plans": L.floor_plans, "features": L.features,
        "transit_min": min(tm.values()) if tm else None,
        "station_km": _station_km(L),
        "parking_rank": _PARK_RANK.get((L.features.get("parking") or {}).get("type")),
        "new": L.uid not in prev,
        "pass": passes, "contribs": contribs,
    }


def build_site(config: dict, store, generated: str) -> str:
    criteria = build_criteria(config.get("criteria", {}))
    filters = [c for c in criteria if c.kind == "filter"]
    scorers = [c for c in criteria if c.kind == "score"]
    browse = {"price_max": 600000, "size_min": 60, **config.get("web", {}).get("browse", {})}
    prev = store.previous_ranks()

    pool = [L for L in store.active_listings() if _in_envelope(L, browse)]
    listings = [_candidate_payload(L, filters, scorers, prev) for L in pool]

    data = {
        "title": config.get("web", {}).get("title", "House Leaderboard"),
        "generated": generated,
        "top_n": config.get("notify", {}).get("email", {}).get("top_n", 5),
        "listings": listings,
        "criteria": ([{"key": c.key, "title": c.title, "kind": "filter"} for c in filters]
                     + [{"key": c.key, "title": c.title, "kind": "score",
                         "weight": c.weight, "priority": c.is_priority} for c in scorers]),
        "i18n": TRANSLATIONS,
        "langs": LANG_NAMES,
    }
    css = (BASE / "static" / "style.css").read_text(encoding="utf-8") + _EXTRA_CSS
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return _TEMPLATE.replace("/*CSS*/", css).replace('"__DATA__"', payload)


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
"""

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>House Leaderboard</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>/*CSS*/</style>
</head>
<body>
<script id="data" type="application/json">"__DATA__"</script>
<div id="app"></div>
<div class="modal-back" id="modalBack"><div class="modal"><div class="modal-inner">
  <button class="modal-close" id="modalClose">&times;</button>
  <div id="modalBody"></div>
</div></div></div>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const EN = DATA.i18n.en;
let lang = localStorage.getItem('lang') || 'en'; if(!DATA.i18n[lang]) lang='en';
function t(k, vars){ let s=(DATA.i18n[lang]||{})[k]||EN[k]||k; if(vars) for(const p in vars) s=s.replace('{'+p+'}', vars[p]); return s; }
const esc = s => (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const euro = n => n==null?'':'€'+Number(n).toLocaleString('fi-FI',{maximumFractionDigits:0});
const euroK = n => n==null?'?':'€'+(n>=1000?Math.round(n/1000)+'k':Math.round(n));
const PARK = {garage:'garage',hall:'parking hall',covered:'carport',own_spot:'own spot',open_pole:'open + heating pole',open:'open spot',none:'no parking'};

// ---- state ----
let mapOn = localStorage.getItem('mapOn')==='1';
let currentSort = localStorage.getItem('sort')||'best';
const FILTER_KEYS = DATA.criteria.filter(c=>c.kind==='filter').map(c=>c.key);
const SCORERS = DATA.criteria.filter(c=>c.kind==='score');
let enabled = new Set(DATA.criteria.map(c=>c.key));   // all on by default

// ---- compute ----
function bd(L){ return L.bedrooms!=null?L.bedrooms:(L.rooms!=null?L.rooms-1:null); }
function scoreOf(L){
  let num=0,den=0,prio=0,pw=0;
  for(const c of L.contribs){ if(!enabled.has(c.key)) continue;
    if(c.prio){ prio+=c.raw*c.weight; pw+=c.weight; } else { num+=c.raw*c.weight; den+=c.weight; } }
  return {score: den?Math.round(1000*num/den)/10:0, priority: pw?prio/pw:0};
}
function included(L){ for(const k of FILTER_KEYS){ if(enabled.has(k) && L.pass[k]===false) return false; } return true; }
const SORTS = {
  best:     r=>[-(r.priority||0),-(r.score||0)],
  price:    r=>[r.L.price==null?Infinity:r.L.price],
  ppm2:     r=>[r.L.ppm2==null?Infinity:r.L.ppm2],
  size:     r=>[r.L.size==null?Infinity:-r.L.size],
  bedrooms: r=>[bd(r.L)==null?Infinity:-bd(r.L)],
  transit:  r=>[r.L.transit_min==null?Infinity:r.L.transit_min],
  station:  r=>[r.L.station_km==null?Infinity:r.L.station_km],
  year:     r=>[r.L.year==null?Infinity:-r.L.year],
  parking:  r=>[r.L.parking_rank==null?Infinity:-r.L.parking_rank],
};
function dedupKey(L){ const a=(L.address||'').toLowerCase().replace(/[^a-z0-9äöå]/g,''); return a?a+'|'+L.size+'|'+L.rooms:null; }
function ranked(){
  let rows = DATA.listings.filter(included).map(L=>{ const s=scoreOf(L); return {L, score:s.score, priority:s.priority}; });
  const f = SORTS[currentSort]||SORTS.best;
  rows.sort((a,b)=>{ const ka=f(a),kb=f(b); for(let i=0;i<ka.length;i++){ if(ka[i]<kb[i])return -1; if(ka[i]>kb[i])return 1; }
    return (b.priority-a.priority)||(b.score-a.score); });
  const seen=new Set();                       // drop the same property cross-posted on both portals
  rows=rows.filter(r=>{ const k=dedupKey(r.L); if(!k) return true; if(seen.has(k)) return false; seen.add(k); return true; });
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
  return '<table>'+rows.join('')+'</table>';
}
function card(r){
  const L=r.L, top=r.rank<=DATA.top_n;
  const img=L.photos&&L.photos[0]?'<img loading="lazy" src="'+esc(L.photos[0])+'" alt="">':'<div class="noimg">—</div>';
  const chips=L.contribs.filter(c=>enabled.has(c.key)).map(c=>'<span class="chip">'+esc(c.label)+'</span>').join('');
  const fa=[L.size!=null?L.size+' m²':'', L.rooms!=null?L.rooms+' '+t('unit_rooms'):'', L.year||'', L.district||L.city].filter(Boolean).map(x=>'<span>'+esc(x)+'</span>').join('');
  return '<article class="card '+(top?'card-top':'')+'" data-id="'+esc(L.id)+'">'
    +'<div class="card-media">'+img+'<span class="rank-badge">#'+r.rank+'</span><span class="score-badge">'+r.score+'</span>'+(L.new?'<span class="new-badge">'+esc(t('badge_new'))+'</span>':'')+'</div>'
    +'<div class="card-body"><div class="card-title">'+esc(L.title)+'</div>'
    +'<div class="card-price">'+euro(L.price)+(L.ppm2?' <span class="ppm2">· '+euro(L.ppm2)+'/m²</span>':'')+'</div>'
    +'<div class="card-facts">'+fa+'</div><div class="card-chips">'+chips+'</div>'
    +'<a class="src-link-card" href="'+esc(L.url)+'" target="_blank" rel="noopener" onclick="event.stopPropagation()">'+esc(t('view_on',{source:L.source}))+'</a></div></article>';
}
function wireCards(root){ root.querySelectorAll('.card').forEach(c=>c.onclick=()=>openModal(c.dataset.id)); }

// ---- render shell ----
function render(){
  document.documentElement.lang=lang;
  const langs=Object.keys(DATA.langs).map(c=>'<a href="#" class="lang '+(c===lang?'active':'')+'" data-lang="'+c+'">'+DATA.langs[c]+'</a>').join('');
  const sortOpts=[['best','sort_best'],['price','sort_price'],['ppm2','sort_ppm2'],['size','sort_size'],['bedrooms','sort_bedrooms'],['transit','sort_transit'],['station','sort_station'],['year','sort_year'],['parking','sort_parking']]
    .map(o=>'<option value="'+o[0]+'"'+(o[0]===currentSort?' selected':'')+'>'+esc(t(o[1]))+'</option>').join('');
  document.getElementById('app').innerHTML=
    '<header class="site-header"><a class="brand" href="#">🏠 '+esc(DATA.title)+'</a>'
    +'<nav><a href="#" id="mapToggle" class="'+(mapOn?'active':'')+'">🗺 '+esc(t('map_view'))+'</a><span class="langs">'+langs+'</span></nav></header>'
    +'<main><div class="board-intro"><h1 id="headline"></h1>'
    +'<p>'+esc(t('index_sub',{top_n:DATA.top_n}))+' <span class="stamp">· '+esc(DATA.generated)+'</span></p></div>'
    +'<div class="controls"><button id="filtersBtn">⚙ '+esc(t('filters'))+'</button>'
    +'<label>'+esc(t('sort_by'))+' <select id="sortSel">'+sortOpts+'</select></label>'
    +'<span class="count" id="count"></span></div>'
    +'<div class="filters-panel" id="filtersPanel"></div>'
    +'<div id="content"></div></main>';
  buildFiltersPanel();
  document.querySelectorAll('.lang').forEach(a=>a.onclick=e=>{e.preventDefault();lang=a.dataset.lang;localStorage.setItem('lang',lang);render();});
  document.getElementById('sortSel').onchange=e=>{currentSort=e.target.value;localStorage.setItem('sort',currentSort);renderContent();};
  document.getElementById('filtersBtn').onclick=()=>document.getElementById('filtersPanel').classList.toggle('open');
  document.getElementById('mapToggle').onclick=e=>{e.preventDefault();mapOn=!mapOn;localStorage.setItem('mapOn',mapOn?'1':'0');render();};
  renderContent();
}
function buildFiltersPanel(){
  const p=document.getElementById('filtersPanel');
  const item=c=>'<label class="chk '+(enabled.has(c.key)?'':'off')+'"><input type="checkbox" data-key="'+c.key+'"'+(enabled.has(c.key)?' checked':'')+'>'+esc(c.title)+'</label>';
  p.innerHTML='<div class="grp">'+esc(t('req_group'))+'</div>'+DATA.criteria.filter(c=>c.kind==='filter').map(item).join('')
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
  const L=DATA.listings.find(x=>x.id===id); if(!L) return; const s=scoreOf(L);
  const gallery=(L.photos||[]).map(p=>'<img loading="lazy" src="'+esc(p)+'">').join('');
  const bars=L.contribs.map(c=>'<li><span class="score-label">'+esc(c.label)+'</span><span class="score-bar"><span style="width:'+Math.round(c.raw*100)+'%"></span></span><span class="score-w">×'+c.weight+'</span></li>').join('');
  const plans=(L.floor_plans||[]).map(p=>'<img loading="lazy" src="'+esc(p)+'">').join('');
  document.getElementById('modalBody').innerHTML=
    '<div class="detail-rank">'+esc(t('rank_score',{rank:'—',score:s.score}))+'</div>'
    +'<h1>'+esc(L.title)+'</h1><div class="detail-addr">'+esc([L.address,L.district,L.city].filter(Boolean).join(', '))+'</div>'
    +'<div class="detail-price">'+euro(L.price)+(L.ppm2?' <span class="ppm2">'+euro(L.ppm2)+'/m²</span>':'')+'</div>'
    +'<a class="src-link" href="'+esc(L.url)+'" target="_blank" rel="noopener">'+esc(t('view_on',{source:L.source}))+'</a>'
    +(gallery?'<div class="gallery" style="margin-top:16px">'+gallery+'</div>':'')
    +'<div class="detail-cols"><section class="facts-box"><h2>'+esc(t('key_facts'))+'</h2>'+facts(L)+'</section>'
    +'<section class="score-box"><h2>'+esc(t('why_rank'))+'</h2><ul class="score-list">'+bars+'</ul></section></div>'
    +(plans?'<section><h2>'+esc(t('floor_plan'))+'</h2><div class="gallery">'+plans+'</div></section>':'')
    +(L.lat&&L.lon?'<section><h2>'+esc(t('location'))+'</h2><div id="dmap" style="height:320px;border-radius:12px"></div></section>':'');
  document.getElementById('modalBack').classList.add('open');
  if(L.lat&&L.lon){ if(window._dm){window._dm.remove();} window._dm=L2map(L); }
}
function L2map(L){ const m=window.L.map('dmap').setView([L.lat,L.lon],14);
  window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(m);
  window.L.marker([L.lat,L.lon]).addTo(m); setTimeout(()=>m.invalidateSize(),50); return m; }
function closeModal(){ document.getElementById('modalBack').classList.remove('open'); if(window._dm){window._dm.remove();window._dm=null;} }
document.getElementById('modalClose').onclick=closeModal;
document.getElementById('modalBack').onclick=e=>{ if(e.target.id==='modalBack') closeModal(); };
render();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
