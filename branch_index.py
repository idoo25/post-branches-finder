"""
In-memory hash-indexed view of the branches table.

Why this exists: SQLite is fast, but Python-side dict/set operations are O(1)
and zero-copy for already-loaded objects. The whole branch table fits in a few
hundred KB — loading it once at startup and serving lookups from memory beats
re-querying SQLite for every request, especially under burst load.

Lookups & complexities:
    by_number[bn]              O(1)  hash lookup
    by_city[name]              O(1)  hash → list[Branch]
    by_service[service_id]     O(1)  hash → set[branch_number]
    has_service(svc_id, bn)    O(1)  set membership
    nearest_haversine(lat,lng,k,filter)
                              O(c) candidate selection via R-Tree
                              + O(c log k) heap-rank
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from heapq import nsmallest
from typing import Iterable

EARTH_RADIUS_M = 6_371_000.0


@dataclass(slots=True)
class Branch:
    branch_number: int
    branch_name: str
    branch_type: str | None
    city: str | None
    full_address: str
    latitude: float
    longitude: float
    lat_rad: float
    lng_rad: float
    sin_lat: float
    cos_lat: float
    telephone: str | None
    services: frozenset[int] = field(default_factory=frozenset)


@dataclass(slots=True)
class NearestHit:
    branch: Branch
    distance_m: float


class BranchIndex:
    """Hash-indexed in-memory branch directory backed by SQLite."""

    __slots__ = ("conn", "by_number", "by_city", "by_service", "_loaded_at")

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.by_number: dict[int, Branch] = {}
        self.by_city: dict[str, list[Branch]] = defaultdict(list)
        self.by_service: dict[int, set[int]] = defaultdict(set)
        self._loaded_at = 0.0
        self._load()

    def _load(self) -> None:
        cur = self.conn.cursor()

        # 1) Pull services-per-branch in one query, group in Python
        svc_by_branch: dict[int, set[int]] = defaultdict(set)
        for bn, sid in cur.execute("SELECT branch_number, service_id FROM branch_services"):
            svc_by_branch[bn].add(sid)
            self.by_service[sid].add(bn)

        # 2) Pull all branches in one query
        rows = cur.execute("""
            SELECT branch_number, branch_name, branch_type, city, full_address,
                   latitude, longitude, lat_rad, lng_rad, sin_lat, cos_lat, telephone
            FROM branches
        """).fetchall()

        for r in rows:
            bn = r["branch_number"]
            b = Branch(
                branch_number=bn,
                branch_name=r["branch_name"],
                branch_type=r["branch_type"],
                city=r["city"],
                full_address=r["full_address"],
                latitude=r["latitude"],
                longitude=r["longitude"],
                lat_rad=r["lat_rad"],
                lng_rad=r["lng_rad"],
                sin_lat=r["sin_lat"],
                cos_lat=r["cos_lat"],
                telephone=r["telephone"],
                services=frozenset(svc_by_branch.get(bn, ())),
            )
            self.by_number[bn] = b
            if b.city:
                self.by_city[b.city].append(b)

        import time as _t
        self._loaded_at = _t.time()

    # ------------------------------------------------------------------
    # O(1) lookups
    # ------------------------------------------------------------------
    def get(self, branch_number: int) -> Branch | None:
        return self.by_number.get(branch_number)

    def with_all_services(self, service_ids: Iterable[int]) -> set[int]:
        """O(k * avg_set_size) — set intersection across required services."""
        sets = [self.by_service.get(sid, set()) for sid in service_ids]
        if not sets:
            return set(self.by_number.keys())
        # smallest set first → cheapest intersection
        sets.sort(key=len)
        result = set(sets[0])
        for s in sets[1:]:
            result &= s
            if not result:
                break
        return result

    # ------------------------------------------------------------------
    # Spatial: R-Tree candidate selection + haversine ranking
    # ------------------------------------------------------------------
    def _bounding_box(self, lat: float, lng: float, radius_m: float) -> tuple[float, float, float, float]:
        # Latitude degree ≈ 111_320 m; longitude degree shrinks by cos(lat).
        dlat = radius_m / 111_320.0
        dlng = radius_m / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
        return (lat - dlat, lat + dlat, lng - dlng, lng + dlng)

    def candidates_in_radius(self, lat: float, lng: float, radius_m: float) -> list[int]:
        """Use the R-Tree index to fetch branch_numbers within a bounding box.
        Returns a *superset* (bbox > circle); haversine is required to refine."""
        min_lat, max_lat, min_lng, max_lng = self._bounding_box(lat, lng, radius_m)
        cur = self.conn.cursor()
        return [r[0] for r in cur.execute(
            "SELECT branch_number FROM branches_rtree "
            "WHERE min_lat >= ? AND max_lat <= ? AND min_lng >= ? AND max_lng <= ?",
            (min_lat, max_lat, min_lng, max_lng),
        )]

    # Two-phase radius ladder, in metres.
    # Phase 1 — fine-grained urban resolution: 0.5, 1, 2, 5, 10, 15, 20 km.
    # Phase 2 — fallback only if phase 1 produced *zero* candidates (rural / negev / golan).
    PRIMARY_LADDER_M:  tuple[int, ...] = (500, 1_000, 2_000, 5_000, 10_000, 15_000, 20_000)
    EXTENDED_LADDER_M: tuple[int, ...] = (30_000, 40_000, 50_000, 75_000, 100_000)

    def nearest_haversine(
        self,
        lat: float,
        lng: float,
        k: int = 20,
        *,
        required_services: Iterable[int] | None = None,
    ) -> list[NearestHit]:
        """k-nearest by great-circle distance.

        Algorithm:
            1. Expanding bounding-box query on R-Tree using the primary ladder
               (0.5 → 20 km). Stop as soon as we have ≥k candidates so we
               don't pay for trig on a large bbox in dense areas.
            2. If primary ladder ends with *zero* candidates (very rural origin),
               escalate to the extended ladder (30 → 100 km).
            3. Optional service-required set intersection.
            4. Heap-based top-k by haversine — O(n log k), better than full sort.
        """
        # Pre-compute origin trig once — used inside the per-candidate hot loop.
        olat_rad = math.radians(lat)
        cos_olat = math.cos(olat_rad)

        svc_filter = self.with_all_services(required_services) if required_services else None

        candidates: list[int] = []
        for radius in self.PRIMARY_LADDER_M:
            candidates = self.candidates_in_radius(lat, lng, radius)
            if svc_filter is not None:
                candidates = [bn for bn in candidates if bn in svc_filter]
            if len(candidates) >= k:
                break

        # Extended ladder kicks in *only* if nothing was found in 20 km.
        if not candidates:
            for radius in self.EXTENDED_LADDER_M:
                candidates = self.candidates_in_radius(lat, lng, radius)
                if svc_filter is not None:
                    candidates = [bn for bn in candidates if bn in svc_filter]
                if len(candidates) >= k:
                    break

        if not candidates:
            return []

        def dist(bn: int) -> float:
            b = self.by_number[bn]
            # Haversine — uses pre-computed sin/cos for the branch.
            dlat = b.lat_rad - olat_rad
            dlng = b.lng_rad - math.radians(lng)
            a = (math.sin(dlat * 0.5) ** 2
                 + cos_olat * b.cos_lat * math.sin(dlng * 0.5) ** 2)
            return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))

        # heapq.nsmallest is O(n log k) — optimal for "top-k from many".
        ranked = nsmallest(k, candidates, key=dist)
        return [NearestHit(branch=self.by_number[bn], distance_m=dist(bn)) for bn in ranked]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        return {
            "branches": len(self.by_number),
            "cities": len(self.by_city),
            "services": len(self.by_service),
            "loaded_at": self._loaded_at,
        }
