# Post Branches DB

SQLite-backed directory of all 1,513 Israel Post branches with a hash-indexed
in-memory layer and a pluggable routing-provider abstraction for "nearest
branch by **real** travel time".

## Files

| File | Purpose |
|------|---------|
| `schema.sql`              | Tables, indexes, R-Tree, performance PRAGMAs |
| `seed_providers.sql`      | Seeds `providers` + `provider_quotas` for every supported service |
| `build_db.py`             | Loads the verified JSON into SQLite (idempotent) |
| `refresh_from_live.py`    | Diffs `post_branches.db` against Israel Post's live branch feed; report-only by default, `--apply` rebuilds the DB from the fresh data |
| `branch_index.py`         | Hash-indexed in-memory directory + spatial nearest-neighbour |
| `address_norm.py`         | Hebrew/English address normalizer for the geocode cache key |
| `address_lookup.py`       | Canonicalizes free-text Israeli addresses against the verified `addresses_db` (city/street/house-number) before geocoding, for a better Nominatim hit rate |
| `providers.py`            | Capability-aware routing/geocoding providers (ORS, Mapbox, Google ×2, OSRM, Valhalla, Waze, Mock, Nominatim) + `RoutingOptions` + `pick_provider` |
| `nearest.py`              | `NearestBranchService` — `find_nearest()` (top-K by time) + `find_within_minutes()` (isochrone-or-matrix) |
| `quota.py`                | `QuotaGuard` — pre-call budget check against sliding window |
| `geo_utils.py`            | UTM-aware buffer, point-in-polygon, encoded-polyline decoder |
| `server.py`               | FastAPI HTTP API wrapping `NearestBranchService` for the React webapp — `/api/autocomplete`, `/api/search`, `/api/branches`, `/api/nearby`, `/api/branch/{n}`, `/api/meta`, plus `/` (serves the built webapp) |
| `webapp/`                 | React + TypeScript frontend (Vite, Leaflet map, RTL Hebrew UI) — see `WEBAPP_README.md` |
| `tests/`                  | Pytest/unittest suite — address normalization, geo utils, quota, routing/geocoding providers, and full-pipeline integration tests (`tests/run_all.py` to run everything) |
| `scripts/bench.py`        | Measured ops/sec for every hot-path operation |
| `scripts/smoke_here.py`   | One-call live smoke test against the real HERE Matrix Routing API |
| `examples/demo_cache_and_quota.py` | End-to-end demo of the geocode cache and quota enforcement |
| `examples/demo_stage1.py` | Stage 1 only — address → 50 nearest branches by air distance, no API call |
| `examples/demo_stage2.py` | Stage 1 + 2 — air-distance shortlist reranked by drive time, incl. auto-batching |
| `examples/demo_stage3.py` | Full 3-tier pipeline (air → drive time → traffic-aware time) with mock providers, runs offline |
| `examples/demo_versatility.py`     | Shows the same `RoutingOptions` flowing through 7 providers |
| `ORS_API_NOTES.md`        | Complete openrouteservice API reference (24 endpoints) |
| `ORS_PATTERNS.md`         | Code recipes from the ORS official examples |
| `WEBAPP_README.md`        | Webapp setup, architecture, and component overview |

## Quick start

```bash
python build_db.py         # creates post_branches.db
python nearest.py          # runs the demo with the mock provider
python server.py           # runs the FastAPI backend on :8000
python -m tests.run_all    # runs the full test suite
python scripts/bench.py    # benchmarks the index
```

See `WEBAPP_README.md` for running the React frontend (`webapp/`).

## Performance (measured on this machine)

| Operation | Time | Throughput |
|---|---:|---:|
| `by_number[bn]`            (O(1) dict)  |  ~0.04 us | 22 M ops/sec |
| `by_service[sid]`          (O(1) set)   |  ~0.05 us | 19 M ops/sec |
| `with_all_services(3 ids)` (set ∩)      |  ~6.5 us  | 155 K ops/sec |
| R-Tree bbox 5 km                        |  ~24 us   | 41 K ops/sec |
| **nearest k=5 (urban)**  full pipeline  |  ~29 us   | 35 K ops/sec |
| nearest k=25 (urban) full pipeline      |  ~138 us  |  7 K ops/sec |
| nearest k=10 random origins             |  ~64 us   | 16 K ops/sec |
| nearest k=5 (rural Negev) w/ ladder     |  ~44 us   | 23 K ops/sec |

**Index load:** all 1,513 branches + 16,570 service-mappings into Python
in ~19 ms.

## Architecture

```
   user address
       │
       ▼
   geocode (cached) ────────► geocode_cache table
       │
       ▼
   R-Tree spatial bbox  ◄──── PRIMARY ladder: 0.5,1,2,5,10,15,20 km
       │                      EXTENDED only-if-empty: 30,40,50,75,100 km
       ▼
   service-set intersection (optional)
       │
       ▼
   travel_time_cache lookup ◄ hits returned for free
       │
       ▼
   routing provider (one batched matrix call for the misses only)
       │
       ▼
   heap-rank by duration → top-k
```

### Why this is fast

1. **R-Tree bounding-box pre-filter** — 1,513 branches → ~25 candidates in
   ~24 us, so the routing API never sees the irrelevant 99 %.
2. **Two-phase radius ladder** — most queries terminate at 0.5–5 km without
   even checking 100 km.
3. **Hash indexes** — all O(1):
   `by_number: dict[int → Branch]`,
   `by_service: dict[int → set[branch_number]]`,
   `by_city: dict[str → list[Branch]]`.
4. **Set intersection for multi-service filters** — sort by smallest set first,
   short-circuit on empty.
5. **`heapq.nsmallest`** — O(n log k), not O(n log n). Matters when you ask for
   top-5 from 25 candidates.
6. **Pre-computed sin/cos for haversine** — skip trig in the hot loop.
7. **Travel-time cache keyed on rounded lat/lng** — same query within ~1 m
   reuses the result.
8. **Batched matrix API call** — one request for N destinations beats N
   single-route calls. All listed providers (Google, Mapbox, OSRM, ORS,
   Valhalla) support this.
9. **WAL + 64 MiB cache + 256 MiB mmap** — SQLite reads from RAM after
   warm-up.
10. **ANALYZE after load** — query planner picks indexes, not full scans.

## Provider abstraction — versatile across services

Every provider declares its capabilities and accepts the same `RoutingOptions`:

```python
class RoutingProvider(Protocol):
    name: str
    supports_matrix:           bool
    supports_isochrone:        bool
    supports_route:            bool
    supports_traffic:          bool
    supports_avoid_polygons:   bool
    supports_avoid_features:   bool
    supports_profile_params:   bool

    def matrix(self, origin, destinations, *, mode, options: RoutingOptions | None = None) -> list[TravelLeg | None]: ...
    def isochrone(self, origin, ranges_seconds, *, mode, options: RoutingOptions | None = None) -> list[IsochroneResult]: ...
    def route(self, origin, destination, *, mode, options: RoutingOptions | None = None) -> TravelLeg | None: ...
```

Each provider honours only the `RoutingOptions` fields it supports and warns
about the rest — the orchestrator can pass the same options to any provider.

### Capability matrix (current)

| Provider                 | matrix | isochrone | route | traffic | avoid_polygons | avoid_features | profile_params |
|--------------------------|:------:|:---------:|:-----:|:-------:|:--------------:|:--------------:|:--------------:|
| `mock_haversine`         |   ✓    |    ✓      |  ✓    |    -    |       -        |       -        |       -        |
| `openrouteservice`       |   ✓    |    ✓      |  ✓    |    -    |       ✓        |       ✓        |       ✓        |
| `mapbox_matrix`          |   ✓    |    ✓      |  ✓    |    ✓    |       -        |       ✓        |       -        |
| `google_routes`          |   ✓    |    -      |  ✓    |    ✓    |       -        |       ✓        |       -        |
| `google_distance_matrix` |   ✓    |    -      |  -    |    ✓    |       -        |       ✓        |       -        |
| `osrm`                   |   ✓    |    -      |  ✓    |    -    |       -        |       -        |       -        |
| `valhalla`               |   ✓    |    ✓      |  ✓    |    -    |       ✓        |       ✓        |       ✓        |
| `waze_deeplink`          |   -    |    -      |  -    |    -    |       -        |       -        |       -        |

Pick the right provider by capability:

```python
from providers import pick_provider, GoogleRoutesProvider, OpenRouteServiceProvider
provider = pick_provider(
    [GoogleRoutesProvider(key1), OpenRouteServiceProvider(key2)],
    supports_traffic=True,
)
```

Switch routing engines by changing one constructor:

```python
NearestBranchService(routing_provider=OpenRouteServiceProvider(api_key=KEY))
NearestBranchService(routing_provider=GoogleRoutesProvider(api_key=KEY))
NearestBranchService(routing_provider=MapboxMatrixProvider(token=TOKEN))
NearestBranchService(routing_provider=OSRMProvider("http://localhost:5000"))
NearestBranchService(routing_provider=ValhallaProvider("http://localhost:8002"))
```

### Two query modes

```python
# Top-K ranked by real travel time (uses Matrix endpoint)
svc.find_nearest("דיזנגוף 50 תל אביב", k=5)

# All branches reachable within N minutes (uses Isochrone if provider has it,
# otherwise falls back to Matrix + duration filter)
svc.find_within_minutes("דיזנגוף 50 תל אביב", minutes=10)

# Either one accepts RoutingOptions for avoidances
from providers import RoutingOptions
from geo_utils import buffer_point_geojson, avoid_polygons_geojson
svc.find_nearest(
    coord, k=5,
    options=RoutingOptions(
        avoid_polygons=avoid_polygons_geojson([
            buffer_point_geojson(34.78, 32.07, 500)["coordinates"][0]   # 500m around X
        ]),
        avoid_features=["tolls", "ferries"],
        profile_params={"weight": 7.5, "height": 2.4},   # for HGV
    ),
)
```

The seeded `providers` table records each provider's:
- `default_cache_ttl_seconds` (Google forbids long-term storage; OSRM has none)
- `supports_traffic` (only Google + Waze SDK)
- `is_self_hosted` (OSRM, Valhalla, Nominatim)
- `notes` (legal/quota constraints)

## On Waze

`waze_deeplink` and `waze_iframe` cannot return travel time — they're
display/navigation tools only. Use them for the "Navigate me there" button
*after* ranking with another provider.

`waze_transport_sdk` supports traffic-aware routing but is partner-only —
requires direct Waze approval.

## Schema highlights

- Branches: precomputed `lat_rad`, `lng_rad`, `sin_lat`, `cos_lat` for haversine.
- `branches_rtree`: SQLite virtual R-Tree on (min_lat, max_lat, min_lng, max_lng).
- `branch_hours`: split into `morning_open/close` + `afternoon_open/close` +
  `closed` flag, for proper "is open at time T?" queries later.
- `travel_time_cache.origin_lat_e5/lng_e5`: lat × 100,000 stored as INTEGER
  → cache hit even with sub-metre GPS jitter, no float-equality pitfalls.
- `routing_requests_log`: one row per outbound API call → cost dashboards.
