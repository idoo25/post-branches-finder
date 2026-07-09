"""
Stage 1 + Stage 2 + Stage 3 demo — 3-tier pipeline
   address  →  50 nearest by air distance      (no API)
            →  15 nearest by drive time        (cheap routing API, e.g. ORS)
            →  10 nearest by traffic-aware time (premium API, e.g. Google)

Demonstrated with mock providers so it runs offline. Wire real keys in
production by passing GoogleDistanceMatrixProvider(api_key=...) and
OpenRouteServiceProvider(api_key=...).
"""
from __future__ import annotations
import sqlite3
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nearest import NearestBranchService
from providers import (Coordinate, GeocodeResult, GoogleDistanceMatrixProvider,
                       MockHaversineProvider, OpenRouteServiceProvider)

DB = Path(__file__).resolve().parent / "post_branches.db"


class FakeGeocoder:
    name = "openrouteservice"
    KNOWN = {
        "דיזנגוף 50 תל אביב": (32.0788, 34.7741),
        "מחנה יהודה ירושלים": (31.7857, 35.2118),
    }
    def geocode(self, address):
        from address_norm import normalize
        n = normalize(address)
        for key, (lat, lng) in self.KNOWN.items():
            if normalize(key) in n or n in normalize(key):
                return GeocodeResult(lat=lat, lng=lng, formatted_address=address)
        return None


def reset_caches():
    conn = sqlite3.connect(DB)
    with conn:
        conn.execute("DELETE FROM travel_time_cache")
        conn.execute("DELETE FROM routing_requests_log")
    conn.close()


def demo_full(svc, address):
    print(f"\n{'='*78}\n  {address}\n{'='*78}")
    print(f"  routing      : {svc.routing.name:25s}  (cap={svc.routing.max_destinations_per_matrix_call})")
    print(f"  traffic      : {svc.traffic.name:25s}  (cap={svc.traffic.max_destinations_per_matrix_call})")

    coord = svc.geocode(address)

    # Stage 1
    t0 = time.perf_counter()
    air = svc.find_nearest_by_air_distance(coord, k=50)
    t1 = (time.perf_counter() - t0) * 1000
    print(f"\n  Stage 1 — air distance        : {len(air):>2} candidates in {t1:6.2f} ms (no API)")
    print(f"      furthest in shortlist: {air[-1].distance_m/1000:.2f} km")

    # Stage 2: 50 → 15 by drive time (cheap)
    t0 = time.perf_counter()
    drive15 = svc.rank_by_drive_time(coord, air, k=15)
    t2 = (time.perf_counter() - t0) * 1000
    cap1 = svc.routing.max_destinations_per_matrix_call
    print(f"\n  Stage 2 — drive time (cheap)  : {len(drive15):>2} ranked in {t2:6.2f} ms "
          f"({(50 + cap1 - 1) // cap1} batch{'es' if (50 + cap1 - 1) // cap1 > 1 else ''})")
    for i, r in enumerate(drive15, 1):
        mins = r.duration_seconds / 60
        print(f"      {i:>2}. #{r.branch.branch_number:<5} {r.branch.branch_name[:30]:<30} "
              f"{r.distance_m/1000:5.2f} km  ~{mins:5.1f} min")

    # Stage 3: 15 → 10 by traffic-aware time (premium)
    t0 = time.perf_counter()
    final10 = svc.rerank_with_traffic(coord, drive15, k=10)
    t3 = (time.perf_counter() - t0) * 1000
    cap2 = svc.traffic.max_destinations_per_matrix_call
    print(f"\n  Stage 3 — TRAFFIC-aware       : {len(final10):>2} ranked in {t3:6.2f} ms "
          f"({(15 + cap2 - 1) // cap2} batch{'es' if (15 + cap2 - 1) // cap2 > 1 else ''})")
    print(f"      *** final answer ***")
    for i, r in enumerate(final10, 1):
        mins_t = (r.duration_in_traffic_seconds or r.duration_seconds) / 60
        mins_b = r.duration_seconds / 60
        delta_pct = (mins_t / mins_b - 1) * 100 if mins_b else 0
        print(f"      {i:>2}. #{r.branch.branch_number:<5} {r.branch.branch_name[:30]:<30} "
              f"{r.distance_m/1000:5.2f} km   "
              f"baseline ~{mins_b:4.1f} min  →  TRAFFIC ~{mins_t:4.1f} min "
              f"({delta_pct:+.0f}%)")

    print(f"\n  Totals:")
    print(f"      time     : {t1+t2+t3:.1f} ms")
    print(f"      ORS  matrix quota left : {svc.quota.remaining(svc.routing.name, 'matrix')}")
    print(f"      Google quota left      : {svc.quota.remaining(svc.traffic.name, 'matrix')}")


def main():
    reset_caches()

    print("─" * 78)
    print(" Setup: ")
    print("   • Stage 1+2 provider: MockHaversineProvider (50 km/h, no traffic)")
    print("   • Stage 3 provider  : MockHaversineProvider with traffic_multiplier=1.5")
    print("                         (simulates Google's live-traffic delays)")
    print("─" * 78)

    svc = NearestBranchService(
        routing_provider=MockHaversineProvider(avg_kmh=50),
        traffic_provider=MockHaversineProvider(avg_kmh=50, traffic_multiplier=1.5),
        geocoding_provider=FakeGeocoder(),
    )

    demo_full(svc, "דיזנגוף 50 תל אביב")
    demo_full(svc, "מחנה יהודה ירושלים")

    print("\n" + "─" * 78)
    print(" One-shot equivalent (3 tiers in one call): find_nearest_with_traffic()")
    print("─" * 78)
    reset_caches()
    t0 = time.perf_counter()
    top10 = svc.find_nearest_with_traffic(
        "דיזנגוף 50 תל אביב",
        air_pool=50, drive_pool=15, final_k=10,
    )
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"\n  Total elapsed: {elapsed:.2f} ms  →  {len(top10)} branches")
    for i, r in enumerate(top10, 1):
        mins_t = (r.duration_in_traffic_seconds or r.duration_seconds) / 60
        print(f"   {i:>2}. #{r.branch.branch_number:<5} {r.branch.branch_name[:30]:<30} "
              f"~{mins_t:.1f} min (with traffic)")
    svc.close()

    print("\n" + "─" * 78)
    print(" To use real APIs in production, just swap providers:")
    print("─" * 78)
    print("""
    svc = NearestBranchService(
        routing_provider=OpenRouteServiceProvider(api_key=ORS_KEY),    # cheap tier
        traffic_provider=GoogleDistanceMatrixProvider(api_key=GMAPS_KEY),  # live traffic
        geocoding_provider=OpenRouteServiceGeocodingProvider(api_key=ORS_KEY),
    )
    top10 = svc.find_nearest_with_traffic("דיזנגוף 50 תל אביב")
    """.rstrip())


if __name__ == "__main__":
    main()
