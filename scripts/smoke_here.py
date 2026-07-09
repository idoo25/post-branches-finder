"""Single-call smoke test for the real HERE Matrix Routing API v8.

Picks a fixed origin (Tel Aviv) and the 15 nearest sample branches by air
distance, then issues exactly ONE HERE matrix request (1×15 = 15 elements).
Prints the result. No retries on failure — we don't want to burn quota.

Run after `HERE_API_KEY` is set in `.env`.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Allow running as a script. This file lives in scripts/, one level below
# the project root where branch_index.py / providers.py / .env / the DB live.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Load .env (server.py also loads it but this script is standalone)
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
if ENV_FILE.exists():
    for ln in ENV_FILE.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

from branch_index import BranchIndex
from providers import Coordinate, HEREMatrixProvider
import sqlite3

HERE_KEY = os.environ.get("HERE_API_KEY")
if not HERE_KEY:
    print("ERROR: HERE_API_KEY missing from .env"); raise SystemExit(2)

DB = Path(__file__).resolve().parent.parent / "post_branches.db"
print(f"DB     : {DB}")
print(f"key    : {HERE_KEY[:6]}…{HERE_KEY[-4:]}  ({len(HERE_KEY)} chars)")

conn = sqlite3.connect(DB, check_same_thread=False)
conn.row_factory = sqlite3.Row
idx = BranchIndex(conn)

# Tel Aviv – Dizengoff 50 (known coordinates)
origin = Coordinate(32.0788, 34.7741)
hits = idx.nearest_haversine(origin.lat, origin.lng, k=15)
print(f"\nshortlist: {len(hits)} branches by air distance")
for h in hits[:5]:
    print(f"  air {h.distance_m/1000:5.2f} km  #{h.branch.branch_number} {h.branch.branch_name}")

destinations = [Coordinate(h.branch.latitude, h.branch.longitude) for h in hits]

print(f"\n→ HERE Matrix Routing v8: 1 origin × {len(destinations)} destinations = "
      f"{len(destinations)} elements, 1 API call")

provider = HEREMatrixProvider(api_key=HERE_KEY)
t0 = time.perf_counter()
try:
    legs = provider.matrix(origin, destinations, mode="driving")
except Exception as e:
    print(f"\nFAILED: {e}"); raise SystemExit(1)
elapsed_ms = (time.perf_counter() - t0) * 1000
print(f"  → {elapsed_ms:.1f} ms")

print(f"\n=== HERE returned {len(legs)} legs (None = unreachable) ===")
ranked = sorted(
    [(h, leg) for h, leg in zip(hits, legs) if leg is not None],
    key=lambda hl: hl[1].duration_in_traffic_seconds or hl[1].duration_seconds,
)
print(f"\n=== Top 10 by HERE traffic-aware time ===")
for i, (h, leg) in enumerate(ranked[:10], 1):
    secs = leg.duration_in_traffic_seconds or leg.duration_seconds
    print(f"  {i:>2}. #{h.branch.branch_number:<5} "
          f"{h.branch.branch_name[:30]:<30}  "
          f"{leg.distance_meters/1000:5.2f} km   ~{secs/60:5.1f} min")

# Also count nones
nones = sum(1 for l in legs if l is None)
if nones:
    print(f"\nUnreachable (errorCode != 0): {nones}")

print("\nSmoke test passed. HERE is correctly wired into the pipeline.")
