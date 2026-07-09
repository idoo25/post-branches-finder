"""
Provider-agnostic geometry helpers — no SDK dependency.

All operations use [longitude, latitude] order (GeoJSON / WGS 84) to match
ALL the routing services we support.
"""
from __future__ import annotations

import math

# UTM zone 36N — covers all of Israel/Palestine. Use a different EPSG abroad.
ISRAEL_UTM_EPSG = 32636
EARTH_RADIUS_M = 6_371_000.0


# ---------------------------------------------------------------------------
# Distance / bearing
# ---------------------------------------------------------------------------
def haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """Great-circle distance between two points, metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Buffer in metres around a WGS-84 point — for `avoid_polygons` payloads
# ---------------------------------------------------------------------------
def buffer_point(lng: float, lat: float, radius_m: float, vertices: int = 16) -> list[list[float]]:
    """Approximate a circle of `radius_m` around (lng,lat) as a closed ring of
    [lng, lat] vertices.

    Implemented analytically (no pyproj dependency). Accurate to <0.5 m for
    radii up to ~5 km in Israel — uses a local equirectangular metric scale at
    the input latitude. For bigger radii use a full UTM transform.
    """
    if vertices < 4:
        raise ValueError("vertices must be >= 4")
    cos_lat = math.cos(math.radians(lat))
    if abs(cos_lat) < 1e-9:
        cos_lat = 1e-9
    # metres per degree
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * cos_lat
    ring = []
    for i in range(vertices):
        theta = 2 * math.pi * i / vertices
        dx = radius_m * math.cos(theta)
        dy = radius_m * math.sin(theta)
        ring.append([lng + dx / m_per_deg_lng, lat + dy / m_per_deg_lat])
    ring.append(ring[0])  # close the ring
    return ring


def buffer_point_geojson(lng: float, lat: float, radius_m: float, vertices: int = 16) -> dict:
    """Same as buffer_point but wrapped as a GeoJSON Polygon — drop into ORS
    `options.avoid_polygons` directly."""
    return {"type": "Polygon", "coordinates": [buffer_point(lng, lat, radius_m, vertices)]}


def avoid_polygons_geojson(polygons: list[list[list[float]]]) -> dict:
    """Wrap many rings as a MultiPolygon — the shape ORS expects."""
    return {"type": "MultiPolygon", "coordinates": [[ring] for ring in polygons]}


# ---------------------------------------------------------------------------
# Point-in-polygon (ray-casting)  — used to filter branches inside an
# isochrone polygon without pulling in shapely.
# ---------------------------------------------------------------------------
def point_in_ring(lng: float, lat: float, ring: list[list[float]]) -> bool:
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and \
           (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def point_in_polygon(lng: float, lat: float, polygon_geojson: dict) -> bool:
    """Works on a GeoJSON Polygon or MultiPolygon. Honours holes correctly."""
    typ = polygon_geojson.get("type")
    coords = polygon_geojson.get("coordinates", [])
    if typ == "Polygon":
        polys = [coords]
    elif typ == "MultiPolygon":
        polys = coords
    else:
        return False
    for poly in polys:
        if not poly:
            continue
        outer = poly[0]
        if not point_in_ring(lng, lat, outer):
            continue
        # check holes — if inside any hole, NOT inside the polygon
        in_hole = any(point_in_ring(lng, lat, hole) for hole in poly[1:])
        if not in_hole:
            return True
    return False


# ---------------------------------------------------------------------------
# Encoded polyline decoder — Google's algorithm, used by Google Maps, ORS
# Optimization, Mapbox (precision 5 default; precision 6 for `polyline6`).
# ---------------------------------------------------------------------------
def decode_polyline(encoded: str, precision: int = 5) -> list[list[float]]:
    """Decode an encoded polyline into a list of [lng, lat] pairs.

    Args:
        encoded   : the ASCII string returned by the provider.
        precision : 5 (Google, ORS, default) or 6 (Mapbox `polyline6`).
    """
    factor = 10 ** precision
    coords: list[list[float]] = []
    index = lat = lng = 0
    n = len(encoded)
    while index < n:
        # Decode latitude delta, then longitude delta.
        for kind in (0, 1):
            result = shift = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if kind == 0:
                lat += delta
            else:
                lng += delta
        coords.append([lng / factor, lat / factor])
    return coords


if __name__ == "__main__":
    # Quick sanity tests
    # 1) Tel Aviv → Eilat distance ≈ 320 km
    d = haversine_m(34.7741, 32.0809, 34.9519, 29.5577)
    print(f"TLV->Eilat haversine: {d/1000:.1f} km  (expected ~320)")

    # 2) Buffer 1 km around Dizengoff has correct sized ring
    ring = buffer_point(34.7741, 32.0809, 1000, vertices=8)
    east_offset_m = haversine_m(34.7741, 32.0809, ring[0][0], ring[0][1])
    print(f"1 km buffer first vertex distance: {east_offset_m:.0f} m  (expected ~1000)")

    # 3) Point in polygon
    poly = buffer_point_geojson(34.7741, 32.0809, 1000)
    print(f"Center inside buffer: {point_in_polygon(34.7741, 32.0809, poly)}  (expected True)")
    print(f"Far point inside buffer: {point_in_polygon(34.9, 32.0809, poly)}  (expected False)")

    # 4) Encoded polyline (Google's classic example "_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    print(f"Decoded sample polyline: {pts}")
