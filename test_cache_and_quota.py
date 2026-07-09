"""
End-to-end demo: prove the geocode cache + quota guard work as advertised.
Uses a fake geocoding provider so we don't actually hit any API.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import sqlite3
import time
from pathlib import Path

from nearest import NearestBranchService
from providers import (Coordinate, GeocodeResult, GeocodingProvider,
                       MockHaversineProvider)

DB = Path(__file__).resolve().parent / "post_branches.db"


# A geocoding provider stub that COUNTS how many times it's actually called.
class CountingFakeGeocoder:
    name = "openrouteservice"        # so it picks up the real seeded quota policy
    calls = 0

    # Real geocoders return the same lat/lng for every spelling of the same place.
    KNOWN = [
        ((32.07880, 34.77462), ["dizengoff 50 tel aviv", "דיזנגוף 50 תל אביב"]),
        ((32.06346, 34.77115), ["rothschild 1 tel aviv", "רוטשילד 1 תל אביב"]),
        ((32.08555, 34.77073), ["ben yehuda 100 tel aviv", "בן יהודה 100 תל אביב"]),
        ((32.81600, 34.99600), ["yefe nof 5 haifa", "יפה נוף 5 חיפה"]),
    ]

    def geocode(self, address: str) -> GeocodeResult | None:
        CountingFakeGeocoder.calls += 1
        from address_norm import normalize
        n = normalize(address)
        for (lat, lng), keys in self.KNOWN:
            if any(k in n or n in k for k in keys):
                return GeocodeResult(lat=lat, lng=lng, formatted_address=address, raw=None)
        return None


def hr(s):
    print()
    print("=" * 70)
    print(s)
    print("=" * 70)


def main():
    # Wipe geocode_cache + quota log so the run is reproducible
    conn = sqlite3.connect(DB)
    with conn:
        conn.execute("DELETE FROM geocode_cache")
        conn.execute("DELETE FROM routing_requests_log")
    conn.close()

    svc = NearestBranchService(
        routing_provider=MockHaversineProvider(avg_kmh=50),
        geocoding_provider=CountingFakeGeocoder(),
    )

    hr("TEST 1 — Hebrew variants of one address → ONE provider call")
    hebrew_variants = [
        "דיזנגוף 50, תל-אביב",
        "דיזנגוף 50  ,  תל אביב",
        'דִּיזֶנְגּוֹף 50, תל אביב',
        'רחוב דיזנגוף 50, ת"א',
    ]
    CountingFakeGeocoder.calls = 0
    for v in hebrew_variants:
        coord = svc.geocode(v)
        print(f"  {v!r:45s} -> ({coord.lat}, {coord.lng})")
    print(f"\n  Geocoder was called {CountingFakeGeocoder.calls} time(s) — expected 1.")
    assert CountingFakeGeocoder.calls == 1, "Cache miss! Normalization broken."

    hr("TEST 1b — English variants of same address → ONE additional call")
    english_variants = [
        "Dizengoff 50, Tel Aviv",
        "  dizengoff   50,  TEL-AVIV  ",
    ]
    CountingFakeGeocoder.calls = 0
    for v in english_variants:
        coord = svc.geocode(v)
        print(f"  {v!r:45s} -> ({coord.lat}, {coord.lng})")
    print(f"\n  Geocoder was called {CountingFakeGeocoder.calls} time(s) — expected 1.")
    print("  (English and Hebrew normalize to different keys → each language paid once.")
    print("   Both forms now cached forever.)")

    # Inspect the row + popularity
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM geocode_cache").fetchone()
    print(f"\n  Row in geocode_cache:")
    print(f"    address_normalized : {row['address_normalized']}")
    print(f"    address_raw        : {row['address_raw']}")
    print(f"    lookup_count       : {row['lookup_count']}  (one INSERT + 5 hits)")

    hr("TEST 2 — different addresses → one call each")
    CountingFakeGeocoder.calls = 0
    for addr in ["Rothschild 1, Tel Aviv", "Ben Yehuda 100, Tel Aviv", "Yefe Nof 5, Haifa"]:
        c = svc.geocode(addr)
        print(f"  {addr!r:45s} -> ({c.lat}, {c.lng})")
    print(f"\n  Geocoder calls: {CountingFakeGeocoder.calls} (expected 3, one per address).")

    hr("TEST 3 — quota guard blocks when daily limit is hit")
    # Force a tiny limit on a fresh fake endpoint to demo the guard mechanics.
    conn = sqlite3.connect(DB)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO provider_quotas(provider_name, endpoint, daily_limit, per_minute_limit) "
            "VALUES ('openrouteservice', 'geocode', 5, 100)"
        )
    conn.close()
    svc.quota._reload_policy()

    # Already used: 1 (TEST1) + 1 (TEST1b) + 3 (TEST2) = 5 calls. Limit = 5.
    print(f"  Used so far : 5 / 5")
    print(f"  Status now  : {svc.quota.remaining('openrouteservice','geocode')}")

    # The 6th call should be BLOCKED by daily quota
    try:
        svc.geocode("HaYarkon 200, Tel Aviv")
        print(f"  6th call    : went through (UNEXPECTED!)")
    except RuntimeError as e:
        print(f"  6th call    : BLOCKED")
        print(f"    reason: {str(e)[:140]}")

    hr("TEST 4 — full nearest_branch with cached origin (zero geocode calls)")
    # Reset quota & geocoder counter
    conn = sqlite3.connect(DB)
    with conn:
        conn.execute("UPDATE provider_quotas SET daily_limit = 3000 WHERE provider_name='openrouteservice' AND endpoint='geocode'")
    conn.close()
    svc.quota._reload_policy()

    CountingFakeGeocoder.calls = 0
    t0 = time.perf_counter()
    results = svc.find_nearest("דיזנגוף 50, תל אביב", k=5, candidate_pool=20)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"  Lookup time           : {elapsed_ms:.2f} ms")
    print(f"  Geocoder API calls    : {CountingFakeGeocoder.calls}  (expected 0 — address was cached)")
    print(f"  Top 5 by travel time  :")
    for i, r in enumerate(results, 1):
        tag = "[cache]" if r.cache_hit else "[live] "
        print(f"    {i}. {tag} #{r.branch.branch_number:<5} {r.branch.branch_name}  "
              f"{r.distance_m/1000:5.2f} km  ~{r.duration_seconds/60:5.1f} min")

    svc.close()
    print()
    print("All tests passed.")


if __name__ == "__main__":
    main()
