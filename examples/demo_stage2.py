"""
Stage 1 + Stage 2 demo: address -> 50 air-distance candidates -> top-30 by drive time.
Shows automatic batching when the candidate count exceeds the provider's per-call cap.
"""
from __future__ import annotations
import sys
import sqlite3
import time
from pathlib import Path

# This file lives in examples/, one level below the project root where
# nearest.py / providers.py / post_branches.db actually live.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nearest import NearestBranchService
from providers import (Coordinate, GeocodeResult, MockHaversineProvider,
                       OpenRouteServiceProvider)


# Offline geocoder so the demo runs without an ORS key.
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


def demo(svc, address, *, candidate_pool=50, top_k=30):
    print()
    print("=" * 78)
    print(f"  {address}")
    print(f"  routing provider : {svc.routing.name}")
    print(f"    per-call cap   : {svc.routing.max_destinations_per_matrix_call}")
    expected_batches = (candidate_pool + svc.routing.max_destinations_per_matrix_call - 1) \
                       // svc.routing.max_destinations_per_matrix_call
    print(f"    expected batches for pool={candidate_pool}: {expected_batches}")
    print("=" * 78)

    # Stage 1
    t0 = time.perf_counter()
    shortlist = svc.find_nearest_by_air_distance(address, k=candidate_pool)
    t_stage1_ms = (time.perf_counter() - t0) * 1000
    print(f"\n  Stage 1 — air distance:  {len(shortlist)} candidates in {t_stage1_ms:.2f} ms (no API)")
    if shortlist:
        print(f"      furthest in shortlist: {shortlist[-1].distance_m/1000:.2f} km")

    # Stage 2
    coord = svc.geocode(address)
    t0 = time.perf_counter()
    ranked = svc.rank_by_drive_time(coord, shortlist, k=top_k)
    t_stage2_ms = (time.perf_counter() - t0) * 1000
    print(f"\n  Stage 2 — drive time:    {len(ranked)} ranked in {t_stage2_ms:.2f} ms")

    # Quota usage report
    rem = svc.quota.remaining(svc.routing.name, "matrix")
    print(f"      matrix quota left: daily={rem['daily_remaining']}/{rem['daily_limit']}, "
          f"minute={rem['minute_remaining']}/{rem['minute_limit']}")

    # Show top 10
    print(f"\n  Top 10 by drive time:")
    for i, r in enumerate(ranked[:10], 1):
        tag = "[cache]" if r.cache_hit else "[live] "
        mins = (r.duration_in_traffic_seconds or r.duration_seconds) / 60
        km   = r.distance_m / 1000
        print(f"   {i:>2}. {tag} #{r.branch.branch_number:<5} "
              f"{r.branch.branch_name[:32]:<32}  {km:5.2f} km  ~{mins:5.1f} min")
    if len(ranked) > 10:
        print(f"   ...")
        last = ranked[-1]
        mins = (last.duration_in_traffic_seconds or last.duration_seconds) / 60
        km = last.distance_m / 1000
        print(f"   {len(ranked):>2}. [{'cache' if last.cache_hit else 'live '}] "
              f"#{last.branch.branch_number:<5} "
              f"{last.branch.branch_name[:32]:<32}  {km:5.2f} km  ~{mins:5.1f} min")


def main():
    # Wipe cache + log so the demo's "live vs cache" tag is meaningful.
    db = Path(__file__).resolve().parent.parent / "post_branches.db"
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("DELETE FROM travel_time_cache")
        conn.execute("DELETE FROM routing_requests_log")
    conn.close()

    print("─" * 78)
    print("  Provider A: MockHaversineProvider (cap=1000 → no batching needed)")
    print("─" * 78)
    svc_mock = NearestBranchService(
        routing_provider=MockHaversineProvider(avg_kmh=45),
        geocoding_provider=FakeGeocoder(),
    )
    demo(svc_mock, "דיזנגוף 50 תל אביב", candidate_pool=50, top_k=30)
    svc_mock.close()

    print()
    print("─" * 78)
    print("  Provider B: OpenRouteServiceProvider (cap=24 → 50 candidates = 3 batches)")
    print("  (Note: the matrix() call would be made over HTTP — we'll only show")
    print("   the orchestration shape; the call itself fails on the stub api_key.)")
    print("─" * 78)
    svc_ors = NearestBranchService(
        routing_provider=OpenRouteServiceProvider(api_key="STUB_KEY"),
        geocoding_provider=FakeGeocoder(),
    )
    print(f"\n  Provider declares max_destinations_per_matrix_call = "
          f"{svc_ors.routing.max_destinations_per_matrix_call}")
    print(f"  For pool=50 the orchestrator will issue ceil(50/24) = "
          f"{(50 + 23) // 24} batches and charge {(50 + 23) // 24} quota units.")

    # Demonstrate just the air-distance stage (Stage 2 needs a real key)
    coord = svc_ors.geocode("דיזנגוף 50 תל אביב")
    shortlist = svc_ors.find_nearest_by_air_distance(coord, k=50)
    print(f"  Stage 1 produced {len(shortlist)} candidates ready for Stage 2.")
    print(f"  Top 5 air-distance: ")
    for i, h in enumerate(shortlist[:5], 1):
        print(f"     {i}. {h.distance_m/1000:5.2f} km  #{h.branch.branch_number:<5} {h.branch.branch_name}")
    print(f"  Furthest in shortlist: {shortlist[-1].distance_m/1000:.2f} km")
    svc_ors.close()


if __name__ == "__main__":
    main()
