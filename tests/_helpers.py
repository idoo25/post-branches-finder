"""Shared test helpers — fake urlopen factory, in-memory DBs, fixtures.

Goal: every test runs offline. No real network call ever fires.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Make the parent package importable when tests run as `python -m unittest`.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# urlopen mock builders
# ---------------------------------------------------------------------------
def fake_urlopen_response(payload: dict, status: int = 200) -> MagicMock:
    """Create a mock that mimics what `urlopen()` returns inside a `with` block.

    Use:
        with patch("urllib.request.urlopen") as mu:
            mu.return_value = fake_urlopen_response({"durations": [...]})
            provider.matrix(...)
    """
    body = json.dumps(payload).encode("utf-8")
    mock = MagicMock()
    mock.read.return_value = body
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=None)
    mock.status = status
    return mock


def captured_request(mock_urlopen) -> tuple:
    """Return (Request, body_dict) the provider sent. Useful in assertions."""
    args, kwargs = mock_urlopen.call_args
    request = args[0]
    body = None
    data = getattr(request, "data", None)
    if data:
        try:
            body = json.loads(data.decode("utf-8"))
        except Exception:
            body = None
    return request, body


# ---------------------------------------------------------------------------
# In-memory test database
# ---------------------------------------------------------------------------
SAMPLE_BRANCHES = [
    # (number, name, lat, lng, city, address)
    (1001, "סניף תל אביב",   32.0809, 34.7741, "תל אביב",  "דיזנגוף 50, תל אביב"),
    (1002, "סניף ירושלים",  31.7857, 35.2118, "ירושלים", "יפו 23, ירושלים"),
    (1003, "סניף חיפה",     32.7940, 34.9896, "חיפה",    "הנשיא 5, חיפה"),
    (1004, "סניף אשקלון",   31.6692, 34.5715, "אשקלון",  "הרצל 18, אשקלון"),
    (1005, "סניף אילת",     29.5577, 34.9519, "אילת",    "התמרים 19, אילת"),
    (1006, "סניף רעננה",    32.1815, 34.8707, "רעננה",   "אחוזה 100, רעננה"),
    (1007, "סניף נתניה",    32.3215, 34.8532, "נתניה",   "הרצל 60, נתניה"),
    (1008, "סניף חולון",    32.0167, 34.7500, "חולון",   "סוקולוב 40, חולון"),
    (1009, "סניף רמת גן",   32.0853, 34.8187, "רמת גן",  "ז'בוטינסקי 50, רמת גן"),
    (1010, "סניף בני ברק",  32.0807, 34.8338, "בני ברק", "רבי עקיבא 80, בני ברק"),
]


def build_test_db() -> sqlite3.Connection:
    """Create an in-memory database with schema + 10 fake branches."""
    import math
    here = Path(__file__).resolve().parents[1]
    schema_sql       = (here / "schema.sql").read_text(encoding="utf-8")
    seed_providers   = (here / "seed_providers.sql").read_text(encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(schema_sql)
    conn.executescript(seed_providers)

    cur = conn.cursor()
    for num, name, lat, lng, city, addr in SAMPLE_BRANCHES:
        lat_rad = math.radians(lat)
        lng_rad = math.radians(lng)
        cur.execute(
            """INSERT INTO branches
               (branch_number, branch_name, branch_type, region, area, city, street,
                house, zip, address_extra, full_address,
                latitude, longitude, lat_rad, lng_rad, sin_lat, cos_lat, telephone)
               VALUES (?, ?, 'סניף', NULL, NULL, ?, ?, NULL, NULL, '', ?,
                       ?, ?, ?, ?, ?, ?, ?)""",
            (num, name, city, addr.split(",")[0], addr,
             lat, lng, lat_rad, lng_rad, math.sin(lat_rad), math.cos(lat_rad), "171"),
        )
        cur.execute(
            "INSERT INTO branches_rtree(branch_number, min_lat, max_lat, min_lng, max_lng) "
            "VALUES (?, ?, ?, ?, ?)",
            (num, lat, lat, lng, lng),
        )
    # Add a couple of services and one branch_services link so queries work.
    cur.executemany(
        "INSERT INTO services(service_id, service_name, category_name) VALUES (?, ?, ?)",
        [(1, "דואר 24", "דואר בארץ"), (2, "כספומט", "כללי")],
    )
    cur.executemany(
        "INSERT INTO branch_services(branch_number, service_id) VALUES (?, ?)",
        [(1001, 1), (1001, 2), (1002, 1), (1003, 1)],
    )
    # Hours: every branch open 09:00–17:00 on Sun-Thu, closed Fri-Sat
    for num, *_ in SAMPLE_BRANCHES:
        for d in range(1, 8):
            if d in (6, 7):
                cur.execute(
                    "INSERT INTO branch_hours(branch_number, day_num, closed) VALUES (?, ?, 1)",
                    (num, d))
            else:
                cur.execute(
                    """INSERT INTO branch_hours(branch_number, day_num, morning_open, morning_close)
                       VALUES (?, ?, '09:00', '17:00')""", (num, d))
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# In-process fake routing provider — no HTTP, fully deterministic
# ---------------------------------------------------------------------------
class FakeRoutingProvider:
    """Returns predictable durations based on a multiplier of haversine distance.

    Useful for end-to-end pipeline tests where you need to know the exact
    expected ranking.
    """
    name = "fake_routing"
    supports_matrix          = True
    supports_isochrone       = False
    supports_route           = False
    supports_traffic         = False
    supports_avoid_polygons  = False
    supports_avoid_features  = False
    supports_profile_params  = False
    max_destinations_per_matrix_call = 1000

    def __init__(self, name: str = "fake_routing", multiplier: float = 1.5,
                 traffic_multiplier: float | None = None):
        self.name = name
        self.multiplier = multiplier
        self.traffic_multiplier = traffic_multiplier
        if traffic_multiplier is not None:
            self.supports_traffic = True
        self.calls = []   # list of (origin, dests) tuples for assertions

    def matrix(self, origin, destinations, *, mode="driving", options=None):
        from geo_utils import haversine_m
        from providers import TravelLeg
        self.calls.append((origin, list(destinations)))
        legs = []
        for d in destinations:
            dist = int(haversine_m(origin.lng, origin.lat, d.lng, d.lat))
            secs = int(dist / 1000 * 60 * self.multiplier)   # multiplier min/km
            traffic_secs = (int(secs * self.traffic_multiplier)
                            if self.traffic_multiplier is not None else None)
            legs.append(TravelLeg(
                duration_seconds=secs,
                duration_in_traffic_seconds=traffic_secs,
                distance_meters=dist,
            ))
        return legs


class FakeGeocoder:
    """In-process geocoder that always returns the same fixed (lat,lng)."""
    name = "fake_geocoder"
    def __init__(self, name="fake_geocoder", lat=32.0809, lng=34.7741, calls=None):
        self.name = name
        self.lat = lat
        self.lng = lng
        self.calls = calls if calls is not None else []
    def geocode(self, address):
        from providers import GeocodeResult
        self.calls.append(address)
        return GeocodeResult(lat=self.lat, lng=self.lng,
                             formatted_address=address, raw=None)


class NoneGeocoder:
    """Always returns None — used to test fallback behaviour in chains."""
    name = "none_geocoder"
    def __init__(self):
        self.calls = []
    def geocode(self, address):
        self.calls.append(address)
        return None
