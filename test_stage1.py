"""
Stage 1 demo — address -> coordinates -> 50 nearest branches by air distance.
NO routing API call. Pure spatial math from the in-memory index.
"""
from __future__ import annotations
import sys
import time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nearest import NearestBranchService
from providers import Coordinate, GeocodeResult, MockHaversineProvider


# Tiny offline geocoder so we don't need an ORS key for the demo.
class FakeGeocoder:
    name = "openrouteservice"
    KNOWN = {
        "דיזנגוף 50 תל אביב":  (32.0788, 34.7741),
        "מחנה יהודה ירושלים":  (31.7857, 35.2118),
        "התמרים 19 אילת":     (29.5577, 34.9519),
        "הרצל 18 אשקלון":      (31.66321, 34.586038),
    }
    def geocode(self, address: str) -> GeocodeResult | None:
        from address_norm import normalize
        n = normalize(address)
        for key, (lat, lng) in self.KNOWN.items():
            if normalize(key) in n or n in normalize(key):
                return GeocodeResult(lat=lat, lng=lng, formatted_address=address)
        return None


def main():
    svc = NearestBranchService(
        routing_provider=MockHaversineProvider(),  # not used in stage 1
        geocoding_provider=FakeGeocoder(),
    )

    addresses = [
        "דיזנגוף 50 תל אביב",
        "מחנה יהודה ירושלים",
        "התמרים 19 אילת",
        "הרצל 18 אשקלון",
    ]

    for addr in addresses:
        print(f"\n=== {addr} ===")
        t0 = time.perf_counter()
        hits = svc.find_nearest_by_air_distance(addr, k=50)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        print(f"  geocoded + ranked {len(hits)} branches in {elapsed_ms:.2f} ms")
        print(f"  closest 10:")
        for i, h in enumerate(hits[:10], 1):
            print(f"   {i:>2}. {h.distance_m/1000:6.2f} km  #{h.branch.branch_number:<5} "
                  f"{h.branch.branch_name:<35} ({h.branch.city})")
        if len(hits) > 10:
            print(f"  ...")
            print(f"   {len(hits):>2}. {hits[-1].distance_m/1000:6.2f} km  #{hits[-1].branch.branch_number:<5} "
                  f"{hits[-1].branch.branch_name:<35} ({hits[-1].branch.city})")

    svc.close()


if __name__ == "__main__":
    main()
