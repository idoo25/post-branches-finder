"""
Demonstrates that the same calling code works against multiple providers,
each honouring only the RoutingOptions fields it supports.
"""
from __future__ import annotations
import sys
import warnings
from pathlib import Path

# This file lives in examples/, one level below the project root where
# providers.py / geo_utils.py actually live.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from providers import (Coordinate, RoutingOptions, MockHaversineProvider,
                       OpenRouteServiceProvider, MapboxMatrixProvider,
                       GoogleRoutesProvider, OSRMProvider, ValhallaProvider,
                       WazeDeepLinkProvider, pick_provider)
from geo_utils import buffer_point_geojson, avoid_polygons_geojson


def hr(t):
    print()
    print("=" * 70)
    print(t)
    print("=" * 70)


def show_capabilities(providers):
    cols = ["matrix", "isochrone", "route", "traffic", "avoid_polygons", "avoid_features", "profile_params"]
    head = f"{'provider':25s} " + " ".join(f"{c:^14s}" for c in cols)
    print(head)
    print("-" * len(head))
    for p in providers:
        row = f"{p.name:25s} "
        for c in cols:
            v = getattr(p, f"supports_{c}", False)
            row += f"{'  yes  ' if v else '   -   ':^14s} "
        print(row)


def main():
    # All providers — most are stubs; the mock is the only one we'll actually call.
    providers = [
        MockHaversineProvider(avg_kmh=50),
        OpenRouteServiceProvider(api_key="STUB"),
        MapboxMatrixProvider(access_token="STUB"),
        GoogleRoutesProvider(api_key="STUB"),
        OSRMProvider(),
        ValhallaProvider(base_url="http://localhost:8002"),
        WazeDeepLinkProvider(),
    ]

    hr("Capability matrix")
    show_capabilities(providers)

    hr("pick_provider — find one that has live traffic + isochrone")
    pick = pick_provider(providers, supports_traffic=True, supports_isochrone=True)
    print(f"  -> {pick.name if pick else 'NONE'}")
    pick = pick_provider(providers, supports_avoid_polygons=True)
    print(f"  Anyone with avoid_polygons? -> {pick.name if pick else 'NONE'}")
    pick = pick_provider(providers, supports_isochrone=True)
    print(f"  Anyone with isochrone? -> {pick.name if pick else 'NONE'}")

    hr("Same RoutingOptions, two providers — see which fields each honours")
    opts = RoutingOptions(
        avoid_polygons=avoid_polygons_geojson([
            buffer_point_geojson(34.7741, 32.0809, 200)["coordinates"][0]  # 200m around Dizengoff
        ]),
        avoid_features=["tolls", "ferries"],
        preference="shortest",
        profile_params={"weight": 7.5, "height": 2.4},
    )
    origin = Coordinate(32.0809, 34.7741)
    dests = [Coordinate(32.0709, 34.7841), Coordinate(32.0909, 34.7641)]

    print("\n[Mock] (no traffic, no avoidances — should warn for everything)")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        legs = providers[0].matrix(origin, dests, mode="driving", options=opts)
        for w in caught:
            print(f"   warning: {w.message}")
    for d, leg in zip(dests, legs):
        print(f"   -> {leg.duration_seconds}s, {leg.distance_meters}m")

    print("\n[ORS] (supports avoid_polygons via /v2/directions, but NOT in matrix — should warn)")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            providers[1].matrix(origin, dests, mode="driving", options=opts)
        except (NotImplementedError, RuntimeError) as e:
            # ORS matrix is implemented but will fail without a real key — fine for the demo.
            print(f"   actual call failed (expected — stub key): {type(e).__name__}")
        for w in caught:
            print(f"   warning: {w.message}")

    hr("Waze deep-link helper — shareable URL for the navigation step")
    waze = providers[-1]
    branch = Coordinate(32.0788, 34.7746)   # Israel Post — Dizengoff
    print(f"   {waze.deep_link(branch)}")


if __name__ == "__main__":
    main()
