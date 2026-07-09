"""
Benchmark the spatial pipeline (no API calls — pure index work).
Run after build_db.py.
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

# This file lives in scripts/, one level below the project root where
# branch_index.py / post_branches.db actually live.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from branch_index import BranchIndex
import sqlite3

DB = Path(__file__).resolve().parent.parent / "post_branches.db"


def warm_then_bench(label, fn, iters=10_000):
    fn()  # warm-up
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    elapsed = time.perf_counter() - t0
    per_op_us = (elapsed / iters) * 1_000_000
    ops_per_sec = iters / elapsed
    print(f"  {label:38s}  {per_op_us:8.2f} us/op   {ops_per_sec:>11,.0f} ops/sec")


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA cache_size = -65536")
    conn.execute("PRAGMA mmap_size = 268435456")

    print("Loading in-memory index...")
    t0 = time.perf_counter()
    idx = BranchIndex(conn)
    print(f"  loaded {len(idx.by_number)} branches in {(time.perf_counter()-t0)*1000:.1f} ms")
    print(f"  by_city buckets: {len(idx.by_city)}, by_service buckets: {len(idx.by_service)}")
    print()

    # Sample 200 random branch coords as test origins
    rng = random.Random(0)
    branches = list(idx.by_number.values())
    sampled = rng.sample(branches, 200)
    coords = [(b.latitude, b.longitude) for b in sampled]

    print("=== HOT-PATH BENCHMARKS ===")
    print()

    # 1) O(1) hash lookup by branch number
    bn = sampled[0].branch_number
    warm_then_bench("by_number[bn]   (dict lookup)", lambda: idx.by_number[bn])

    # 2) O(1) service membership
    sid = next(iter(idx.by_service))
    warm_then_bench("by_service[sid]  (set lookup)", lambda: idx.by_service[sid])

    # 3) Set intersection: branches with services {2,4,11}
    svcs = [2, 4, 11]
    warm_then_bench("with_all_services({2,4,11})", lambda: idx.with_all_services(svcs))

    # 4) R-Tree bbox query (5 km)
    lat, lng = coords[0]
    warm_then_bench("rtree bbox 5 km", lambda: idx.candidates_in_radius(lat, lng, 5_000))

    # 5) Full nearest_haversine k=5  (urban origin)
    warm_then_bench(
        "nearest k=5 (urban)",
        lambda: idx.nearest_haversine(32.0809, 34.7741, k=5),
        iters=5_000,
    )

    # 6) Full nearest_haversine k=25 (urban)
    warm_then_bench(
        "nearest k=25 (urban)",
        lambda: idx.nearest_haversine(32.0809, 34.7741, k=25),
        iters=5_000,
    )

    # 7) Full nearest_haversine across many random origins (mixed difficulty)
    j = [0]
    def random_nearest():
        lat, lng = coords[j[0] % len(coords)]
        j[0] += 1
        return idx.nearest_haversine(lat, lng, k=10)
    warm_then_bench("nearest k=10 (200 mixed origins)", random_nearest, iters=2_000)

    # 8) Rural fallback (Negev)
    warm_then_bench(
        "nearest k=5 (rural Negev)",
        lambda: idx.nearest_haversine(30.6, 34.7, k=5),
        iters=2_000,
    )

    print()
    print("All numbers measured on the in-memory layer + R-Tree.")
    print("No routing-API calls were made (so no traffic / network noise).")


if __name__ == "__main__":
    main()
