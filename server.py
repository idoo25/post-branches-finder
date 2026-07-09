"""
HTTP API wrapping NearestBranchService for the React webapp.

Run:
    pip install fastapi uvicorn
    set ORS_API_KEY=...           (optional — falls back to mock)
    set GOOGLE_API_KEY=...        (optional — falls back to mock)
    python server.py

Endpoints:
    GET  /api/autocomplete?q=...           — Pelias autocomplete (ORS)
    POST /api/search    {address}          — top-10 by traffic-aware time
    GET  /api/branches                      — lightweight list of all branches
    POST /api/nearby    {address}          — top-k by straight-line distance only
    GET  /api/branch/{branch_number}        — full branch details
    GET  /api/meta                          — build/database metadata
    GET  /                                  — serves the static React build
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nearest import NearestBranchService
from providers import (ChainedGeocodingProvider, Coordinate,
                       GoogleDistanceMatrixProvider, HEREMatrixProvider,
                       MockHaversineProvider, NominatimProvider, OSRMProvider,
                       OpenRouteServiceProvider,
                       OpenRouteServiceGeocodingProvider)

# Free real-routing fallback for /api/search, tried when ORS itself is out of
# quota: the public OSRM demo server (router.project-osrm.org), routing over
# OpenStreetMap data. No API key, but it's shared community infra — the
# per-minute self-throttle lives in provider_quotas, not here.
_OSRM_PROVIDER = OSRMProvider()

# Final, quota-free safety net for /api/search: straight-line distance ÷ a
# typical driving speed. Used only when routing AND traffic AND OSRM are all
# unavailable — clearly labeled as an estimate, never silently passed off as real.
_ESTIMATE_PROVIDER = MockHaversineProvider(avg_kmh=35)


HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "post_branches.db"
WEB_DIST = HERE / "webapp" / "dist"      # produced by `npm run build`

# Tiny .env loader (no python-dotenv dependency).
_ENV_FILE = HERE / ".env"
if _ENV_FILE.exists():
    for ln in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        os.environ.setdefault(k, v)

ORS_KEY    = os.environ.get("ORS_API_KEY")
GOOGLE_KEY = os.environ.get("GOOGLE_API_KEY")
HERE_KEY   = os.environ.get("HERE_API_KEY")


# ---------------------------------------------------------------------------
# Build providers (real if keys present, mocks otherwise)
# ---------------------------------------------------------------------------
def _build_service() -> NearestBranchService:
    routing = (OpenRouteServiceProvider(api_key=ORS_KEY)
               if ORS_KEY else MockHaversineProvider(avg_kmh=50))
    # Stage 3 — preferred order: HERE (live traffic, generous free tier) → Google → none.
    # We never use a mock for Stage 3 because fake "traffic" data is misleading.
    if HERE_KEY:
        traffic = HEREMatrixProvider(api_key=HERE_KEY)
    elif GOOGLE_KEY:
        traffic = GoogleDistanceMatrixProvider(api_key=GOOGLE_KEY)
    else:
        traffic = None
    # Geocoding chain — Nominatim is much better at Hebrew street/house addresses
    # in Israel than ORS Pelias, which only resolves to city level. ORS is kept
    # as a fallback (and for autocomplete suggestions, which Nominatim lacks).
    chain = [NominatimProvider(user_agent="post-branches-finder/1.0", country="il")]
    if ORS_KEY:
        chain.append(OpenRouteServiceGeocodingProvider(api_key=ORS_KEY))
    geocoding = ChainedGeocodingProvider(chain, name="nominatim")
    return NearestBranchService(
        routing_provider=routing, traffic_provider=traffic,
        geocoding_provider=geocoding, db_path=DB_PATH,
    )


svc = _build_service()
print(f"[server] routing  = {svc.routing.name}  (key={'real' if ORS_KEY else 'MOCK'})")
print(f"[server] traffic  = {svc.traffic.name if svc.traffic else 'DISABLED — set GOOGLE_API_KEY to enable Stage 3'}")
print(f"[server] geocoder = {svc.geocoding.name if svc.geocoding else 'none (cache-only)'}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _branch_to_dict(b, conn: sqlite3.Connection) -> dict:
    """Hydrate the full branch payload (hours + services + accessibility)."""
    bn = b.branch_number
    cur = conn.cursor()
    hours = [
        {"day_num": r[0], "morning_open": r[1], "morning_close": r[2],
         "afternoon_open": r[3], "afternoon_close": r[4], "closed": bool(r[5])}
        for r in cur.execute(
            "SELECT day_num, morning_open, morning_close, afternoon_open, afternoon_close, closed "
            "FROM branch_hours WHERE branch_number = ? ORDER BY day_num", (bn,))
    ]
    services_by_cat: dict[str, list[str]] = {}
    for cat, name in cur.execute("""
        SELECT s.category_name, s.service_name FROM branch_services bs
        JOIN services s ON s.service_id = bs.service_id WHERE bs.branch_number = ?
        ORDER BY s.category_name IS NULL, s.category_name, s.service_name""", (bn,)):
        services_by_cat.setdefault(cat or "כללי", []).append(name)
    extras = [r[0] for r in cur.execute(
        "SELECT extra FROM branch_extra_services WHERE branch_number = ?", (bn,))]
    accessibility = [r[0] for r in cur.execute(
        "SELECT accessibility_type FROM branch_accessibility WHERE branch_number = ?", (bn,))]

    full_address = b.full_address
    cur.execute("SELECT zip FROM branches WHERE branch_number = ?", (bn,))
    row = cur.fetchone()
    return {
        "branch_number": bn,
        "branch_name":   b.branch_name,
        "branch_type":   b.branch_type,
        "city":          b.city,
        "full_address":  full_address,
        "zip":           (row[0] if row else None),
        "latitude":      b.latitude,
        "longitude":     b.longitude,
        "telephone":     b.telephone,
        "hours":         hours,
        "services":      services_by_cat,
        "extra_services": extras,
        "accessibility": accessibility,
    }


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
app = FastAPI(title="Post Branches Finder")
app.add_middleware(
    CORSMiddleware,
    # 5173 is Vite's configured dev port (webapp/vite.config.ts), but Vite
    # auto-increments to 5174/5175/... whenever the configured port is
    # already taken, so allow a small range rather than just the one port.
    # The GitHub Pages origin is the deployed frontend's real production origin.
    allow_origins=[f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in range(5173, 5178)]
    + ["https://idoo25.github.io"],
    allow_methods=["*"], allow_headers=["*"],
)


class SearchRequest(BaseModel):
    address: str = ""
    lat: float | None = None
    lng: float | None = None
    # Bounded well below the ~1.5k-row branch table: air_pool is the cheap/free
    # haversine shortlist, drive_pool feeds a billed routing-matrix call, and
    # final_k feeds the premium traffic-matrix call — each cap keeps a single
    # request from enumerating (near-)the whole table and firing dozens of
    # billed provider calls.
    air_pool:   int = Field(default=50, gt=0, le=200)
    drive_pool: int = Field(default=20, gt=0, le=50)
    final_k:    int = Field(default=10, gt=0, le=25)


@app.get("/api/autocomplete")
def autocomplete(q: str = Query(..., min_length=2), size: int = Query(default=5, gt=0, le=20)):
    """Pelias autocomplete via ORS. Falls back to local 'starts with' search
    over our cached/known addresses if ORS is unavailable."""
    if svc.geocoding is None:
        return {"suggestions": []}
    try:
        results = svc.geocoding.autocomplete(q, size=size)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"suggestions": [
        {"label": r.formatted_address or q, "lat": r.lat, "lng": r.lng}
        for r in results
    ]}


@app.post("/api/search")
def search(req: SearchRequest):
    """Run the 3-tier pipeline and return enriched results for the UI."""
    if req.lat is not None and req.lng is not None:
        # Caller already resolved a coordinate (e.g. the user picked an
        # autocomplete suggestion) — reuse it instead of re-geocoding the
        # suggestion's label text through a *different* provider. Re-geocoding
        # mismatched labels (ORS Pelias's formatted string fed into Nominatim)
        # is what produced wrong-city results for ambiguous place names.
        coord = Coordinate(req.lat, req.lng)
    else:
        if not req.address.strip():
            raise HTTPException(status_code=400, detail="address or lat/lng is required")
        try:
            coord = svc.geocode(req.address)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=f"geocoding unavailable: {e}")
        except Exception:
            raise HTTPException(status_code=502, detail="geocoding unavailable")
        if coord is None:
            raise HTTPException(status_code=404, detail="Address not resolved")

    routing_used = svc.routing.name
    traffic_used = svc.traffic.name if svc.traffic else None
    is_estimate = False
    if svc.traffic is not None:
        # Try the full 3-tier pipeline (Stage 2 ORS, Stage 3 HERE/Google).
        try:
            ranked = svc.find_nearest_with_traffic(
                coord, air_pool=req.air_pool, drive_pool=req.drive_pool, final_k=req.final_k,
            )
        except RuntimeError as e:
            # If Stage 2 routing is down (ORS proxy 500s, etc.) we degrade
            # to a 2-tier pipeline: air-distance shortlist (drive_pool) → HERE
            # directly. HERE's traffic-aware times are also a valid ranking,
            # so the user still gets quality results — just without the
            # cheaper ORS pre-filter.
            try:
                air_hits = svc.find_nearest_by_air_distance(coord, k=req.drive_pool)
                ranked = svc.rerank_with_traffic(coord, air_hits, k=req.final_k)
                # Stage 2 (ORS) was skipped entirely in this branch — don't
                # leave routing_used pointing at a provider that was never
                # actually called for this request.
                routing_used = "air-distance-only"
                print(f"[server] Stage 2 unavailable ({e}); fell back to "
                      f"air-distance + traffic ({len(air_hits)} → {len(ranked)})")
            except RuntimeError as e2:
                # Stage 3 (the traffic provider itself) is the one failing —
                # e.g. quota exhausted — so retrying it is pointless. Degrade
                # further to Stage 1+2 only (no live traffic).
                try:
                    ranked = svc.find_nearest(
                        coord, k=req.final_k, candidate_pool=req.air_pool,
                    )
                    traffic_used = None
                    print(f"[server] Traffic provider unavailable ({e2}); fell back "
                          f"to routing-only ranking, no live traffic ({len(ranked)} results)")
                except RuntimeError as e3:
                    # ORS is also out of quota — try OSRM (free, OpenStreetMap-
                    # based public demo, no API key) before giving up on real
                    # routing entirely.
                    try:
                        air_hits = svc.find_nearest_by_air_distance(coord, k=req.final_k)
                        ranked = svc._matrix_rerank(
                            coord, air_hits, req.final_k,
                            provider=_OSRM_PROVIDER, mode="driving", options=None,
                        )
                        routing_used = _OSRM_PROVIDER.name
                        traffic_used = None
                        print(f"[server] Routing provider unavailable ({e3}); fell back "
                              f"to OSRM (free, OSM-based) ({len(ranked)} results)")
                    except RuntimeError as e4:
                        # OSRM's public demo is also down/rate-limited — final,
                        # quota-free safety net: rank by straight-line distance
                        # divided by a typical driving speed. Not real travel
                        # time, but the search must never just 500 on the user.
                        air_hits = svc.find_nearest_by_air_distance(coord, k=req.final_k)
                        ranked = svc._matrix_rerank(
                            coord, air_hits, req.final_k,
                            provider=_ESTIMATE_PROVIDER, mode="driving", options=None,
                        )
                        routing_used = _ESTIMATE_PROVIDER.name
                        traffic_used = None
                        is_estimate = True
                        print(f"[server] OSRM also unavailable ({e4}); fell back "
                              f"to estimated travel time (air distance ÷ {_ESTIMATE_PROVIDER.avg_kmh} km/h)")
    else:
        # No traffic provider configured — top-K straight from routing.
        ranked = svc.find_nearest(
            coord, k=req.final_k, candidate_pool=req.air_pool,
        )

    out = []
    for i, r in enumerate(ranked, 1):
        out.append({
            "rank":           i,
            "branch_number":  r.branch.branch_number,
            "branch_name":    r.branch.branch_name,
            "city":           r.branch.city,
            "full_address":   r.branch.full_address,
            "telephone":      r.branch.telephone,
            "latitude":       r.branch.latitude,
            "longitude":      r.branch.longitude,
            "distance_km":    round(r.distance_m / 1000, 2),
            "duration_min":   round(r.duration_seconds / 60, 1),
            "duration_in_traffic_min": (
                round(r.duration_in_traffic_seconds / 60, 1)
                if r.duration_in_traffic_seconds is not None else None),
            "cache_hit":      r.cache_hit,
        })

    return {
        "origin": {"lat": coord.lat, "lng": coord.lng},
        "providers": {
            "routing":  routing_used,
            "traffic":  traffic_used,
            "geocoder": svc.geocoding.name if svc.geocoding else None,
        },
        "is_estimate": is_estimate,
        "results": out,
    }


@app.get("/api/branches")
def all_branches():
    """Every branch, lightweight fields only — served straight from the
    in-memory index (no routing/geocoding API, no per-request DB hit)."""
    return {"branches": [
        {
            "branch_number": b.branch_number,
            "branch_name":   b.branch_name,
            "branch_type":   b.branch_type,
            "city":          b.city,
            "full_address":  b.full_address,
            "telephone":     b.telephone,
            "latitude":      b.latitude,
            "longitude":     b.longitude,
        }
        for b in svc.index.by_number.values()
    ]}


class NearbyRequest(BaseModel):
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    k: int = Field(default=15, gt=0, le=100)


@app.post("/api/nearby")
def nearby(req: NearbyRequest):
    """Top-k branches by straight-line distance only — no routing or traffic
    API call. The expanding-radius ladder (0.5..20km, then 30..100km if still
    empty) lives in BranchIndex.nearest_haversine and makes this adaptive."""
    if req.lat is not None and req.lng is not None:
        coord = Coordinate(req.lat, req.lng)
    elif req.address and req.address.strip():
        try:
            coord = svc.geocode(req.address)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=f"geocoding unavailable: {e}")
        except Exception:
            raise HTTPException(status_code=502, detail="geocoding unavailable")
        if coord is None:
            raise HTTPException(status_code=404, detail="Address not resolved")
    else:
        raise HTTPException(status_code=400, detail="address or lat/lng is required")

    hits = svc.find_nearest_by_air_distance(coord, k=req.k)
    out = [
        {
            "rank":           i,
            "branch_number":  h.branch.branch_number,
            "branch_name":    h.branch.branch_name,
            "city":           h.branch.city,
            "full_address":   h.branch.full_address,
            "telephone":      h.branch.telephone,
            "latitude":       h.branch.latitude,
            "longitude":      h.branch.longitude,
            "distance_km":    round(h.distance_m / 1000, 2),
        }
        for i, h in enumerate(hits, 1)
    ]
    return {"origin": {"lat": coord.lat, "lng": coord.lng}, "results": out}


@app.get("/api/meta")
def meta():
    """Build/database metadata (written by build_db.py into db_meta)."""
    cur = svc.conn.cursor()
    rows = cur.execute("SELECT key, value FROM db_meta").fetchall()
    return {r[0]: r[1] for r in rows}


@app.get("/api/branch/{branch_number}")
def branch_detail(branch_number: int):
    b = svc.index.get(branch_number)
    if b is None:
        raise HTTPException(status_code=404, detail="branch not found")
    return _branch_to_dict(b, svc.conn)


# ---------------------------------------------------------------------------
# Static frontend (after `npm run build` produces webapp/dist)
# ---------------------------------------------------------------------------
if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
else:
    @app.get("/")
    def root_placeholder():
        return {
            "message": "API is running. Build the React app:  cd webapp && npm install && npm run build",
            "endpoints": [
                "GET /api/autocomplete?q=...",
                "POST /api/search",
                "GET /api/branches",
                "POST /api/nearby",
                "GET /api/branch/{n}",
                "GET /api/meta",
            ],
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
