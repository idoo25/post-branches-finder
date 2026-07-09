# openrouteservice — Complete API Reference

**Source of truth:** `https://docs.openrouteservice.org/all/docs` — the live, merged OpenAPI 3.0.1 spec for all HeiGIT services. 716 KB. Pulled and parsed deterministically (no AI summarization in the loop).

```
API server: https://api.openrouteservice.org
Spec mirror: https://docs.openrouteservice.org/{service}/{version}/docs
```

---

## Authentication

| Method | Where | Endpoints that accept it |
|---|---|---|
| Query parameter `?api_key=<KEY>` | URL | All `GET` endpoints (geocode, directions GET, elevation/point GET) |
| Header `Authorization: <KEY>` (no `Bearer ` prefix!) | HTTP header | All endpoints (POST + GET) |

## Coordinate convention
**ALL endpoints use `[longitude, latitude]`** (GeoJSON / WGS 84 EPSG:4326). Opposite of Google Maps.

## Routing profiles
`driving-car` (default) · `driving-hgv` · `cycling-regular` · `cycling-road` · `cycling-mountain` · `cycling-electric` · `foot-walking` · `foot-hiking` · `wheelchair`

---

# Service catalog (24 endpoints across 11 services)

## 1. Directions Service — `Directions Service`

### `GET /v2/directions/{profile}`
| Param | In | Type | Required |
|---|---|---|---|
| `profile` | path | string | ✓ |
| `start` | query | string `lng,lat` | ✓ |
| `end` | query | string `lng,lat` | ✓ |
| `api_key` | query | string | ✓ (or use header) |

### `POST /v2/directions/{profile}` · `/json` · `/geojson` · `/gpx`
All four POST variants share the **identical body schema** (only the response format differs):

| Field | Type | Default | Notes |
|---|---|---|---|
| **`coordinates`** | `array<[lng,lat]>` | — | **REQUIRED**. Up to 50 waypoints. |
| `id` | string | — | Echoes back in the metadata. |
| `preference` | enum | `recommended` | `fastest` · `shortest` · `recommended` · **`custom`** |
| `units` | enum | `m` | `m` · `km` · `mi` |
| `language` | enum | `en` | 28 languages incl. **`he`/`he-il`**, `en`, `de`, `ru`, `fr`, `es`, `ar`(?)... |
| `geometry` | bool | `true` | Return route polyline |
| `geometry_simplify` | bool | `false` | Cannot be used with route ≥ 1 alternative. |
| `instructions` | bool | `true` | Turn-by-turn |
| `instructions_format` | enum | `text` | `text` · `html` |
| `roundabout_exits` | bool | `false` | Adds `exit_bearings` array to step |
| `maneuvers` | bool | `false` | Include maneuver object in steps |
| `attributes` | array<string> | — | `avgspeed` · `detourfactor` · `percentage` |
| `extra_info` | array<string> | — | `surface`/`steepness`/`waytype`/`tollways`/`waycategory`/`green`/`noise`/... |
| `radiuses` | array<number> | — | Per-waypoint snap-to-road radius in metres |
| `bearings` | array<[deg,dev]> | — | Restrict approach direction per waypoint |
| `continue_straight` | bool | `false` | Restrict u-turns at waypoints |
| `elevation` | bool | — | Add z-axis to geometry |
| `options` | object | — | Advanced routing (avoid_features, profile_params, …) |
| `suppress_warnings` | bool | — | Quiet warnings in response |
| `skip_segments` | array<int> | — | Skip these segments (1-based pair indices) |
| `alternative_routes` | object | — | `{target_count, weight_factor, share_factor}` |
| `maximum_speed` | number | — | User-set speed cap |
| **`custom_model`** | object | — | Required when `preference=custom`. Per-edge weighting model. |
| **`schedule`** | bool | `false` | **Public-transport schedule** mode |
| `schedule_duration` | string | — | ISO duration window |
| `schedule_rows` | integer | — | Max schedule entries returned |
| `walking_time` | string | — | Max walking duration in PT mode |
| `ignore_transfers` | bool | `false` | PT: ignore transfer count when ranking |

---

## 2. Matrix Service — `POST /v2/matrix/{profile}`

| Field | Type | Default | Notes |
|---|---|---|---|
| **`locations`** | `array<[lng,lat]>` | — | **REQUIRED** |
| `id` | string | — | Echo |
| `sources` | array<string \| int> | `all` | indices into `locations` |
| `destinations` | array<string \| int> | `all` | indices into `locations` |
| `metrics` | array<enum> | `["duration"]` | `distance` · `duration` |
| `resolve_locations` | bool | `false` | Adds street name to source/destination items |
| `units` | enum | `m` | `m` · `km` · `mi` (only affects distance) |

Response:
```json
{ "durations":[[s,...]], "distances":[[m,...]],
  "destinations":[{"location":[lng,lat],"snapped_distance":m,"name":"..."}, ...],
  "sources":     [{"location":[lng,lat],"snapped_distance":m,"name":"..."}, ...],
  "metadata":    {...} }
```
`null` cells = unreachable.

---

## 3. Isochrones Service — `POST /v2/isochrones/{profile}`

| Field | Type | Default | Notes |
|---|---|---|---|
| **`locations`** | `array<[lng,lat]>` | — | **REQUIRED** |
| **`range`** | `array<number>` | — | **REQUIRED**. seconds (time) or metres (distance) |
| `id` | string | — | Echo |
| `range_type` | enum | `time` | `time` · `distance` |
| `units` | enum | `m` | for distance ranges |
| `area_units` | enum | `m` | unit of returned `area` attribute |
| `location_type` | enum | `start` | `start` · `destination` |
| `interval` | number | — | Step (only when `range` has 1 value) |
| `attributes` | array<enum> | — | `area` · `reachfactor` · `total_pop` |
| `intersections` | bool | `false` | Return intersecting polygons too |
| `smoothing` | number | — | 0–1 generalisation factor |
| `options` | object | — | Same routing-options block as Directions |

---

## 4. Snapping Service — `POST /v2/snap/{profile}` · `/json` · `/geojson`

| Field | Type | Default | Notes |
|---|---|---|---|
| **`locations`** | `array<[lng,lat]>` | — | **REQUIRED** |
| **`radius`** | number | — | **REQUIRED**. Search radius in metres. |
| `id` | string | — | Echo |

GeoJSON variant adds a `source_id` property to each Feature so you can map outputs back to inputs (0-based).

---

## 5. Export Service — `POST /v2/export/{profile}` · `/json` · `/topojson`

Returns the underlying graph (points/edges/weights) within a bbox.

| Field | Type | Default | Notes |
|---|---|---|---|
| **`bbox`** | `array<[lng,lat]>` | — | **REQUIRED**. Two-corner bounding box. |
| `id` | string | — | Echo |
| `geometry` | bool | `true` | true = exact geometry; false = beeline (faster) |

---

## 6. Matching Service — `/v2/match/{profile}` 🆕

*This service was missing from my earlier docs — only revealed by the full spec.*

### `GET /v2/match/{profile}` — service info ping
### `POST /v2/match/{profile}` — map-match a GPS trace

| Field | Type | Notes |
|---|---|---|
| `key` | string | API key (alternative to header) |
| `features` | object | GeoJSON FeatureCollection of LineString/Point with the GPS trace |
| `id` | string | Echo |

Use case: snap a noisy GPS trace to the road network.

---

## 7. POIs Service — `POST /pois`

| Field | Type | Default | Notes |
|---|---|---|---|
| **`request`** | enum | — | **REQUIRED**. `pois` (list) · `stats` (counts by category) · `list` (categories tree) |
| **`geometry`** | object | example bbox | **REQUIRED**. `{bbox?, geojson?, buffer?}` — bbox or buffered point/line/polygon |
| `filters` | object | — | OSM tag filters: `category_ids`, `category_group_ids`, `name`, `wheelchair`, `smoking`, `fee`, etc. |
| `limit` | integer | — | Max results |
| `sortby` | enum | — | `category` · `distance` |

---

## 8. Optimization — `POST /optimization`

Vehicle Routing Problem solver (Vroom).

| Field | Type | Notes |
|---|---|---|
| **`jobs`** | array<object> | **REQUIRED**. Each job: `id`, `location:[lng,lat]`, `service` (sec), `priority`, `pickup`/`delivery`, `skills`, `time_windows`, `setup`, `description`, `location_index` |
| **`vehicles`** | array<object> | **REQUIRED**. Each vehicle: `id`, `start`/`end`/`profile`, `capacity`, `skills`, `time_window`, `breaks`, `steps` |
| `matrices` | object | **Custom pre-computed matrices per profile** — bypass live routing |
| `options` | object | Solver tunables |

Conventions: `[lon, lat]`, seconds, metres, `time_window=[start_epoch, end_epoch]`.

---

## 9. Elevation — `/elevation/line` · `/elevation/point`

### `POST /elevation/line`
| Field | Type | Default | Notes |
|---|---|---|---|
| **`format_in`** | enum | — | **REQUIRED**. `geojson` · `polyline` · `encodedpolyline5` · `encodedpolyline6` |
| **`geometry`** | object | — | **REQUIRED**. The line in the chosen `format_in` |
| `format_out` | enum | `geojson` | same enum as `format_in` |
| `dataset` | enum | `srtm` | only `srtm` available on hosted tier |

### `GET /elevation/point`
| Param | Type | Required |
|---|---|---|
| `geometry` | array `[lng,lat]` | ✓ |
| `api_key` | string | ✓ |
| `format_out` | string | — |
| `dataset` | string | — |

### `POST /elevation/point`
| Field | Type | Default | Notes |
|---|---|---|---|
| **`format_in`** | enum | — | **REQUIRED**. `geojson` · `point` |
| **`geometry`** | object | — | **REQUIRED** |
| `format_out` | enum | `geojson` | |
| `dataset` | enum | `srtm` | |

---

## 10. Geocode — Pelias-backed (`/geocode/...`)

### `GET /geocode/search` — forward
| Param | Required | Notes |
|---|---|---|
| `api_key` | ✓ | |
| `text` | ✓ | the free-text query |
| `focus.point.lon`, `focus.point.lat` | — | bias scoring within ~100 km |
| `boundary.country` | — | ISO-3166 alpha-2 or -3 (use `ISR`) |
| `boundary.rect.{min_lon,min_lat,max_lon,max_lat}` | — | hard bbox filter |
| `boundary.circle.{lon,lat,radius}` | — | radius (km) |
| `boundary.gid` | — | Pelias gid (limit to known administrative area) |
| `sources` | — | `osm` · `oa` · `wof` · `gn` |
| `layers` | — | `address`/`venue`/`street`/`locality`/`region`/`postalcode`/`country`/... |
| `size` | — | result count (default 10) |

### `GET /geocode/autocomplete`
Same as search **minus** `boundary.circle.*`, `boundary.gid`, `size`. **Throttle requests; responses are async.**

### `GET /geocode/search/structured` (beta)
Adds: `address`, `neighbourhood`, `borough`, `locality`, `county`, `region`, `postalcode`, `country`. (Plus all the `boundary.*` and `focus.*` params.)

### `GET /geocode/reverse`
| Param | Required | Notes |
|---|---|---|
| `api_key` | ✓ | |
| `point.lon`, `point.lat` | ✓ | |
| `boundary.circle.radius` | — | km |
| `boundary.country` | — | |
| `sources`, `layers`, `size` | — | |

Response: GeoJSON `FeatureCollection`. Coords at `features[i].geometry.coordinates = [lng, lat]`. Properties include `label`, `country`, `region`, `locality`, `postalcode`, `confidence`.

---

## 11. Health & Status

| Endpoint | Use |
|---|---|
| `GET /v2/health` | liveness probe |
| `GET /v2/status` | service status |

---

# Free-tier limits (per endpoint)

| Section | Daily | Per minute | Hard cap per request |
|---|---:|---:|---|
| Directions | 2,000 | 40 | 50 waypoints, ≤6,000 km |
| Export | 100 | 5 | 10 km² bbox |
| Isochrones | 500 | 20 | 5 locations, ≤120 km, 10 intervals |
| Matrix | 500 | 40 | 3,500 origin×dest (50×50); 25 with dynamic args |
| Snap | 2,000 | 100 | 5,000 locations |
| Pois | 500 | 60 | 50 km² area, 2 km radius |
| Optimization | 500 | 40 | 50 jobs, 3 vehicles |
| Elevation | 2,000 | 40 | 2,000 vertices |
| Geocode | 3,000 | 100 | — |
| Match (Map-matching) | — | — | not on free-tier dashboard |

---

# Things I had missed in earlier passes

The user asked "did you really cover the whole API?" — answer was no. Pulling the full spec deterministically caught these:

1. 🆕 **Matching Service** (`/v2/match/{profile}`) — completely missing from my earlier nav
2. **`preference: custom`** — there's a 4th preference value (not just fastest/shortest/recommended)
3. **`custom_model`** — per-edge weighting, required for `preference=custom`
4. **`schedule`/`schedule_duration`/`schedule_rows`/`walking_time`/`ignore_transfers`** — Directions can return **public-transport schedules**, not just routes
5. **`maximum_speed`** — user-controlled speed cap
6. **`alternative_routes`** — `{target_count, weight_factor, share_factor}` for multiple route suggestions
7. **`skip_segments`** — skip specific segment indices
8. **28 supported `language` codes**, including **`he`/`he-il`** for Hebrew turn-by-turn instructions
9. **POIs body** — `request: pois|stats|list`, `filters`, `limit`, `sortby`. Full schema, not just "geometry input"
10. **Optimization `matrices` field** — pre-compute custom matrices and feed them in (saves money on subsequent solves)
11. **Geocode `boundary.gid`** — restrict by Pelias administrative gid
12. **Snap `radius` is REQUIRED**, not optional
13. **`bearings`** for Directions — restrict approach direction at waypoints
14. **All `extra_info` types**: `surface`/`steepness`/`waytype`/`tollways`/`waycategory`/`green`/`noise`/`countryinfo`/...

---

# Code wiring

| Concept | Where |
|---|---|
| `Authorization: <key>` POST/GET | [`providers.py:OpenRouteServiceProvider.matrix`](providers.py) |
| `[lng, lat]` order | same — `[origin.lng, origin.lat]` |
| Forward geocode (`/geocode/search`) | [`providers.py:OpenRouteServiceGeocodingProvider.geocode`](providers.py) |
| Per-endpoint quota policy | [`seed_providers.sql`](seed_providers.sql) → `provider_quotas` |
| 1-unit-per-request matrix billing | [`nearest.py`](nearest.py) — `quota.allow(..., units=1)` |

The full raw spec is cached at `%TEMP%/ors_full.json` (716 KB) — re-run the curl any time it changes.
