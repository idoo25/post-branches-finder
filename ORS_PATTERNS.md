# openrouteservice — Patterns from the official examples

Distilled from 7 worked notebooks at `openrouteservice.org/example-*/`. Each pattern is the actual code shape — adapted to what's useful for **our** post-branch finder, with notes on what to copy vs. skip.

---

## Pattern A — Avoid polygons (`options.avoid_polygons`)
*Examples: `example-avoid-obstacles-while-routing`, `example-avoid-flooded-areas-with-ors`, `dieselgate-avoid-berlin-banned-diesel-streets`.*

The same trick three times: route between A and B while skipping a set of forbidden zones (construction, flooded areas, banned-diesel streets).

```python
from shapely.geometry import LineString, MultiPolygon, mapping

# 1) Get a baseline route
route = ors.directions(
    coordinates=[A, B],
    profile='driving-car',
    preference='shortest',
    format_out='geojson',
)

# 2) Buffer the route ~50–100 m so we only consider obstacles "near" it
route_line = LineString(route['features'][0]['geometry']['coordinates'])
route_buffer = route_line.buffer(0.0009)   # ~100 m in WGS84 degrees (cheap & cheerful)

# 3) Keep only obstacles that intersect the buffer
obstacles_in_path = [poly for poly in all_obstacle_polygons if route_buffer.intersects(poly)]

# 4) Re-request with avoid_polygons set
route_alt = ors.directions(
    coordinates=[A, B],
    profile='driving-car',
    preference='shortest',
    options={'avoid_polygons': mapping(MultiPolygon(obstacles_in_path))},
)
```

### Iterative refinement (flooded-areas notebook)
After getting `route_alt`, check again if NEW obstacles intersect the new buffer; if so, append + repeat.

### Caveat from the notebooks
> "GET requests can only contain so many characters. > 80 polygons exceed the limit. POST endpoint sidesteps this — but practical max is ~15-20 polygons in a GET URL."

We're using POST, so we're fine — but the **principle stays**: keep the avoid-polygon list small, filter to "near the route" first.

### When useful for the branch finder
- Closed roads / temporary flooding around a branch
- Areas with restricted vehicle access (low-emission zones — like Tel-Aviv's planned LEZ)
- Walking-only zones (Carmel Market, Mahane Yehuda) — feed those polygons to driving requests

---

## Pattern B — Point → buffered polygon
*Helper used in nearly every example.*

Converting a single GPS point into a small polygon for `avoid_polygons`. The trick: buffer in **metres** by re-projecting to a metric CRS first.

```python
import pyproj
from shapely.geometry import Point

def buffer_point(lng, lat, radius_m, resolution=2):
    to_metric  = pyproj.Transformer.from_crs("epsg:4326", "epsg:32636")  # UTM 36N for Israel
    to_wgs84   = pyproj.Transformer.from_crs("epsg:32636", "epsg:4326")
    x, y = to_metric.transform(lng, lat)
    polygon = Point(x, y).buffer(radius_m, resolution=resolution)
    return [to_wgs84.transform(*p) for p in polygon.exterior.coords]
```

> Notebooks use **EPSG:32632** (UTM 32N — Germany). For **Israel**, use **EPSG:32636** (UTM 36N) — that's the right zone for the country.
> `resolution=2` keeps the polygon to ~8 vertices (octagon-ish). Bigger resolution = smoother circle but more vertices = bigger payload.

---

## Pattern C — Isochrones for "what's reachable in N minutes"
*From: `example-apartment-search-with-ors`.*

Apartment search asks: "what's within 15 min walk?". Same shape gives us "what branches are within 10 min drive?".

```python
iso = ors.isochrones(
    locations=[[user_lng, user_lat]],
    profile='foot-walking',          # or 'driving-car' for our use
    range=[900],                     # seconds — 15 min
    attributes=['total_pop'],        # optional bonus info
)
# iso['features'][0]['geometry'] is a Polygon you can:
#   - draw on a map
#   - feed as filter to /pois (apartment example)
#   - test individual branch coords against (`shapely.contains`)
```

### When useful for the branch finder
This is **a competing strategy** to "spatial-then-routing":
- Old way: take 25 nearest-by-air → matrix call → pick fastest
- Isochrone way: one isochrone call → list all branches inside it → done, no per-branch routing

**Trade-off**: isochrone gives you a yes/no answer per branch, no actual travel times. If the user wants "top 5 ranked by minutes", you still need matrix.
**Quota cost**: 1 isochrone request vs 1 matrix request — roughly the same.

---

## Pattern D — POIs API (category filter inside an arbitrary polygon)
*From: apartment-search and pub-crawl notebooks.*

The POIs endpoint takes any polygon and returns OSM features filtered by category.

```python
pubs = ors.places(
    request='pois',
    geojson=any_polygon_geojson,    # e.g. an isochrone, a city boundary, a bbox
    filter_category_ids=[569],      # 569 = pub. Use category_list ['list'] to discover IDs
    sortby='distance',
)['features']

# Custom OSM tag filter on top
pubs_smoker = ors.places(
    request='pois',
    geojson=any_polygon_geojson,
    filter_category_ids=[569],
    filters_custom={'smoking': ['yes']},
    sortby='distance',
)
```

Each feature has `properties.osm_tags` (name, opening_hours, phone, wheelchair, …) and `properties.category_ids`.

### Not directly useful for the branch finder
Israel Post branches are not OSM POIs (they're in the official Israel Post DB we already imported). We'd use `/pois` if we wanted "show me ATMs / supermarkets near the branch I'm sending the user to", as bonus context.

---

## Pattern E — Matrix → ortools TSP
*From: `example-optimize-pub-crawl-with-ors`.*

Get an N×N matrix from ORS, feed it as the distance callback to ortools — get the optimal visit order (Travelling Salesman).

```python
from ortools.constraint_solver import pywrapcp

# 1) Matrix: N pubs × N pubs durations (seconds)
matrix = ors.distance_matrix(
    locations=[[lng, lat] for lng, lat in pubs_coords],   # [lng,lat]!
    profile='driving-car',
    metrics=['duration'],
)
# matrix['durations'][i][j]  = seconds from pub i to pub j

# 2) Wire to ortools
N = len(pubs_coords)
manager = pywrapcp.RoutingIndexManager(N, num_vehicles=1, depot=0)
routing = pywrapcp.RoutingModel(manager)

def cost(from_idx, to_idx):
    return int(matrix['durations'][manager.IndexToNode(from_idx)][manager.IndexToNode(to_idx)])

routing.SetArcCostEvaluatorOfAllVehicles(
    routing.RegisterTransitCallback(cost)
)
solution = routing.Solve()

# 3) Read out the optimal node order
node = routing.Start(0)
order = []
while not routing.IsEnd(node):
    order.append(manager.IndexToNode(node))
    node = solution.Value(routing.NextVar(node))
```

### Quota math from the notebook
> "Matrix call gave 24×24 = 576 elements in 1 request, then ortools is local."

So one matrix call buys you the full distance graph for arbitrarily many vehicles/orderings — you don't need to keep calling ORS during the optimization itself.

### Caveat from the notebook
> "Matrix is restricted to 5×5 locations for biking/walking profiles. driving-car has the full 50×50."

Confirmed limit — don't use `foot-walking` for big TSPs.

### When useful for the branch finder
**Probably not** for the standard "user → 1 nearest branch" flow. **But** if you ever add a feature like "today I need to visit the post and 3 errands — give me the optimal order", this is the recipe.

---

## Pattern F — Optimization endpoint (Vroom VRP)
*From: `disaster-optimization`.*

If Pattern E is "one vehicle, find the best order", this is "many vehicles + constraints, schedule them all". The cyclone example: 3 vehicles, 20 delivery points, time windows, vehicle capacities.

```python
import openrouteservice as ors

vehicles = [
    ors.optimization.Vehicle(
        id=i,
        start=[depot_lng, depot_lat],
        capacity=[300],
        time_window=[unix_8am, unix_8pm],
    )
    for i in range(3)
]

jobs = [
    ors.optimization.Job(
        id=row.id,
        location=[row.lon, row.lat],
        service=1200,                   # 20 min on site
        amount=[row.units_needed],
        time_windows=[[row.open_from_unix, row.open_to_unix]],
    )
    for row in delivery_data.itertuples()
]

result = ors_client.optimization(jobs=jobs, vehicles=vehicles, geometry=True)

# Walk the schedule
for route in result['routes']:
    for step in route['steps']:
        print(step.get('job', 'Depot'), step['arrival'], step.get('service', 0))

# Decode the route polyline (it's encoded, not GeoJSON)
geom = ors.convert.decode_polyline(route['geometry'])
```

### Key ideas
- `time_window` and `time_windows` are **POSIX timestamps** `[start, end]` (seconds since epoch)
- `geometry=True` makes ORS auto-fetch the actual polyline for each leg (ORS internally calls Directions for you)
- Response geometry is **encoded polyline** — needs `ors.convert.decode_polyline(...)`
- ORS Optimization is **a wrapper over Vroom** — the data shape is Vroom's

### When useful for the branch finder
**Not for the basic "find nearest branch" flow.** Worth knowing if you ever add a logistics product (e.g. couriers picking up packages from N branches in optimal order).

---

## Pattern G — Export endpoint → graph analysis (networkx)
*From: `centrality-analysis-using-export-endpoint`.*

Pull the underlying road graph for a bbox, build a NetworkX `DiGraph`, run any graph algorithm.

```python
import openrouteservice as ors
import networkx as nx
import geojson

resp = ors_client.request(
    url=f'/v2/export/driving-car',
    get_params={},
    post_json={'bbox': [[lng_min, lat_min], [lng_max, lat_max]]},
)
# resp['nodes'][i] = {'nodeId': N, 'location': [lng, lat]}
# resp['edges'][i] = {'fromId': N, 'toId': N, 'weight': W}

G = nx.DiGraph()
for n in resp['nodes']:
    G.add_node(n['nodeId'], pos=n['location'])
for e in resp['edges']:
    G.add_edge(e['fromId'], e['toId'], weight=e['weight'])

centralities = nx.edge_betweenness_centrality(G, weight='weight')
# → which road segments are most "essential" to the local network
```

### When useful for the branch finder
- Identifying which streets near a branch are the bottleneck if closed
- "What if X bridge is closed, which branch becomes harder to reach?" simulation
- Probably **out of scope for now** — but a great answer if a planning team ever asks.

### Quota cost
Export is rate-limited at **100 req/day, 5/min, ≤10 km² per call** — this is a research tool, not a per-user request.

---

# Cross-cutting takeaways

| Insight | Source | Code impact |
|---|---|---|
| Coordinates are **always `[lng, lat]`** in ORS | Every example | Already enforced in `OpenRouteServiceProvider` |
| Buffer in metres = re-project to UTM, buffer, project back | A, B, C | If we add "avoid X area" feature: use **EPSG:32636** for Israel |
| Decode optimization geometry with `ors.convert.decode_polyline` | F | Add util when we build a routes layer |
| `ors.directions` accepts `format_out='geojson'` so you can hand it straight to folium / leaflet | A,D,F | We currently extract only duration/distance; could enrich to return geometry too |
| Use **isochrone instead of matrix** when you only need yes/no reachability within N minutes | C | Could add `.find_within_minutes(addr, minutes)` as a cheaper alternative API |
| Matrix is the right tool when ranking; isochrone is the right tool when filtering | E vs C | Both fit our app — different endpoints for different UX |
| `route['features'][0]['properties']['summary']` has `duration` and `distance` for the whole leg | A,D | What we already extract |
| `5×5` matrix cap on biking/walking — full 50×50 only on `driving-car` | E | If we ever add walking-mode for users without cars — keep candidate_pool ≤ 5 |
| Encoded polyline vs GeoJSON: directions can be either; optimization is **always encoded** | F | Note when we wire it |

---

# Suggested additions to our code (not done yet)

1. **`utils.py:buffer_point_il(lng, lat, radius_m)`** — UTM-36N buffer helper, ready when we want to avoid-areas
2. **`providers.py:OpenRouteServiceProvider.isochrone(coord, minutes)`** — alternative to matrix when ranking isn't needed
3. **`providers.py:decode_polyline(encoded)`** — copy `ors.convert.decode_polyline` so we don't need the openrouteservice-py dependency
4. **`nearest.py:find_within_minutes(addr, minutes, mode)`** — uses isochrone + spatial filter, not matrix; cheaper for the "what's reachable" use case

Tell me which (if any) you want me to wire now.
