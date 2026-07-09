"""
End-to-end "find nearest branch by real travel time".

Pipeline:
    user address  →  geocode (cached)
                  →  R-Tree spatial candidates  (no API call)
                  →  travel-time cache lookup    (no API call)
                  →  routing provider for misses (one batched matrix call)
                  →  min-heap rank by duration
                  →  top-k

Provider-agnostic: pass any object that satisfies providers.RoutingProvider.
Swap providers by changing the constructor argument — no other code changes.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
import warnings
from dataclasses import dataclass
from heapq import nsmallest
from pathlib import Path

from address_lookup import canonicalize as canonicalize_address
from address_norm import normalize as normalize_address
from branch_index import Branch, BranchIndex, NearestHit
from geo_utils import haversine_m, point_in_polygon
from providers import (Coordinate, GeocodingProvider, RoutingOptions,
                       RoutingProvider, TravelLeg)
from quota import QuotaGuard

DB_PATH = Path(__file__).resolve().parent / "post_branches.db"
ORIGIN_PRECISION = 5  # 5 decimals ≈ 1.1 m — same query rounds to same cache key


# ----------------------------------------------------------------------------
# Result type
# ----------------------------------------------------------------------------
@dataclass(slots=True)
class RankedBranch:
    branch: Branch
    distance_m: int
    duration_seconds: int
    duration_in_traffic_seconds: int | None
    cache_hit: bool


@dataclass(slots=True)
class ReachableBranch:
    """A branch confirmed inside an isochrone polygon, no per-branch routing needed."""
    branch: Branch
    range_seconds: int          # which isochrone band it falls in
    haversine_m: int            # straight-line distance to origin (for sorting)


# ----------------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------------
class NearestBranchService:
    """The high-level finder. One instance per process."""

    __slots__ = ("conn", "index", "routing", "traffic", "geocoding", "quota",
                 "_cache_locks", "_cache_locks_guard")

    def __init__(
        self,
        routing_provider: RoutingProvider,
        geocoding_provider: GeocodingProvider | None = None,
        traffic_provider: RoutingProvider | None = None,
        db_path: Path = DB_PATH,
    ):
        # check_same_thread=False so the same NearestBranchService can serve
        # requests from FastAPI's threadpool. SQLite + WAL handles the locking.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Performance pragmas (per-connection)
        self.conn.execute("PRAGMA cache_size = -65536")
        self.conn.execute("PRAGMA mmap_size = 268435456")
        # FK enforcement is a per-connection SQLite setting, NOT persisted in
        # schema.sql — must be re-set on every connection we open ourselves.
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.index = BranchIndex(self.conn)
        self.routing = routing_provider
        # Optional traffic-aware provider (e.g. Google) used in rerank_with_traffic.
        # If None, rerank_with_traffic requires it to be passed explicitly.
        self.traffic = traffic_provider
        self.geocoding = geocoding_provider
        self.quota = QuotaGuard(self.conn)
        # Per-cache-key locks so two concurrent requests for the identical
        # geocode/matrix key serialize on the (paid) provider call instead of
        # both missing the cache and both billing/consuming quota.
        self._cache_locks: dict[str, threading.Lock] = {}
        self._cache_locks_guard = threading.Lock()

    def _lock_for_key(self, key: str) -> threading.Lock:
        """Get (or create) the lock for a given cache key. Creation is
        guarded by one small meta-lock; the returned per-key lock is what
        callers actually hold while they check-then-populate the cache."""
        with self._cache_locks_guard:
            lock = self._cache_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._cache_locks[key] = lock
            return lock

    # ------------------------------------------------------------------
    # Geocoding (cached + quota-guarded)
    # ------------------------------------------------------------------
    def geocode(self, address: str) -> Coordinate | None:
        """Returns coords from cache or provider. None if unresolvable.

        Tries, in order:
            1. The official form from address_db (verified city/street/
               house-number data), if the input matches anything there.
            2. The same, but with one adjacent word-pair in the street name
               merged together — Israeli OSM data sometimes tags a multi-word
               Hebrew street as partially concatenated (e.g. "בנציון ישראלי"
               instead of the official "ישראלי בן ציון"). Nominatim's
               tokenizer is order-insensitive but won't bridge a merged word,
               so every adjacent pair is tried (bounded: street-word-count − 1
               extra attempts) rather than guessing which one.
            3. The original, unmodified input.
        First candidate that resolves wins; each attempt still benefits from
        the geocode_cache (see `_geocode_one`).
        """
        candidates = [canonical] if (canonical := canonicalize_address(address)) else []
        if canonical:
            m = re.match(r"^(.*?)\s+(\d+),\s*(.+)$", canonical)
            if m:
                street, house, city = m.groups()
                words = street.split(" ")
                for i in range(len(words) - 1):
                    merged_words = words[:i] + [words[i] + words[i + 1]] + words[i + 2:]
                    candidates.append(f"{' '.join(merged_words)} {house}, {city}")
        candidates.append(address)

        for candidate in candidates:
            coord = self._geocode_one(candidate)
            if coord is not None:
                return coord
        return None

    def _geocode_cache_lookup(self, norm: str) -> Coordinate | None:
        """SELECT by normalized form, honouring expires_at (mirrors
        travel_time_cache's TTL check). On hit, bump lookup_count +
        last_used_at and return; on miss/expired, return None."""
        row = self.conn.execute(
            "SELECT latitude, longitude FROM geocode_cache "
            "WHERE address_normalized = ? "
            "AND (expires_at IS NULL OR expires_at > strftime('%s','now'))",
            (norm,),
        ).fetchone()
        if not row:
            return None
        # Cheap popularity bump — same row, no INSERT.
        with self.conn:
            self.conn.execute(
                "UPDATE geocode_cache SET lookup_count = lookup_count + 1, "
                "last_used_at = CURRENT_TIMESTAMP WHERE address_normalized = ?",
                (norm,),
            )
        return Coordinate(row["latitude"], row["longitude"])

    def _geocode_one(self, address: str) -> Coordinate | None:
        """Cache flow for a single address string:
            1. Normalize the input (Hebrew niqqud strip, abbreviations, etc.).
            2. SELECT by normalized form (PK lookup, O(log n)), skipping
               expired rows.
            3. On hit → bump lookup_count + last_used_at, return.
            4. On miss → check provider quota → call → INSERT with a fresh
               expires_at.
        """
        norm = normalize_address(address)
        if not norm:
            return None

        coord = self._geocode_cache_lookup(norm)
        if coord is not None:
            return coord

        if self.geocoding is None:
            return None  # no provider wired → cache-only mode

        # Serialize concurrent requests for the identical address so only
        # one of them ever pays for the provider call; the loser re-checks
        # the (now populated) cache instead of calling the provider again.
        with self._lock_for_key(f"geocode:{norm}"):
            coord = self._geocode_cache_lookup(norm)
            if coord is not None:
                return coord

            # Quota check before paying for the call.
            if not self.quota.allow(self.geocoding.name, "geocode"):
                st = self.quota.status(self.geocoding.name, "geocode")
                raise RuntimeError(
                    f"Quota exceeded for {self.geocoding.name}/geocode "
                    f"({st.reason}; retry in {st.retry_after_s:.0f}s). "
                    f"Daily {st.daily_used}/{st.daily_limit}, minute {st.minute_used}/{st.minute_limit}."
                )

            t0 = time.perf_counter()
            try:
                result = self.geocoding.geocode(address)
            except Exception:
                self.quota.refund(self.geocoding.name, "geocode")
                raise
            dur_ms = int((time.perf_counter() - t0) * 1000)

            with self.conn:
                self.conn.execute(
                    """INSERT INTO routing_requests_log
                       (provider, request_type, num_destinations, status_code,
                        elements_billed, duration_ms)
                       VALUES (?, 'geocode', 1, ?, 1, ?)""",
                    (self.geocoding.name, 200 if result else 404, dur_ms),
                )

            if result is None:
                return None

            ttl = self._provider_ttl(self.geocoding.name)
            expires = int(time.time()) + ttl if ttl > 0 else None

            with self.conn:
                self.conn.execute(
                    """INSERT INTO geocode_cache
                       (address_normalized, address_raw, latitude, longitude,
                        formatted_address, provider, expires_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(address_normalized) DO UPDATE SET
                         address_raw = excluded.address_raw,
                         latitude = excluded.latitude,
                         longitude = excluded.longitude,
                         formatted_address = excluded.formatted_address,
                         provider = excluded.provider,
                         fetched_at = CURRENT_TIMESTAMP,
                         expires_at = excluded.expires_at,
                         lookup_count = lookup_count + 1,
                         last_used_at = CURRENT_TIMESTAMP""",
                    (norm, address, result.lat, result.lng,
                     result.formatted_address, self.geocoding.name, expires),
                )
            return Coordinate(result.lat, result.lng)

    # ------------------------------------------------------------------
    # Travel-time cache helpers
    # ------------------------------------------------------------------
    def _cache_key(self, lat: float, lng: float) -> tuple[int, int]:
        scale = 10 ** ORIGIN_PRECISION
        return (round(lat * scale), round(lng * scale))

    def _fetch_cached_legs(
        self,
        origin: Coordinate,
        branch_numbers: list[int],
        mode: str,
        provider_name: str,
    ) -> dict[int, TravelLeg]:
        if not branch_numbers:
            return {}
        olat_e5, olng_e5 = self._cache_key(origin.lat, origin.lng)
        placeholders = ",".join("?" * len(branch_numbers))
        rows = self.conn.execute(
            f"""SELECT branch_number, duration_seconds, duration_in_traffic_seconds, distance_meters
                FROM travel_time_cache
                WHERE origin_lat_e5 = ? AND origin_lng_e5 = ?
                  AND mode = ? AND provider = ?
                  AND (expires_at IS NULL OR expires_at > strftime('%s','now'))
                  AND branch_number IN ({placeholders})""",
            (olat_e5, olng_e5, mode, provider_name, *branch_numbers),
        ).fetchall()
        return {
            r["branch_number"]: TravelLeg(
                duration_seconds=r["duration_seconds"],
                duration_in_traffic_seconds=r["duration_in_traffic_seconds"],
                distance_meters=r["distance_meters"],
                raw=None,
            )
            for r in rows
        }

    def _store_legs(
        self,
        origin: Coordinate,
        branch_numbers: list[int],
        legs: list[TravelLeg | None],
        mode: str,
        provider_name: str,
        ttl_seconds: int,
    ) -> None:
        olat_e5, olng_e5 = self._cache_key(origin.lat, origin.lng)
        expires = int(time.time()) + ttl_seconds if ttl_seconds > 0 else None
        rows = []
        for bn, leg in zip(branch_numbers, legs):
            if leg is None:
                continue
            rows.append((
                olat_e5, olng_e5, bn, mode, provider_name,
                leg.duration_seconds, leg.duration_in_traffic_seconds,
                leg.distance_meters,
                json.dumps(leg.raw, ensure_ascii=False) if leg.raw else None,
                expires,
            ))
        if not rows:
            return
        with self.conn:
            self.conn.executemany(
                """INSERT INTO travel_time_cache
                   (origin_lat_e5, origin_lng_e5, branch_number, mode, provider,
                    duration_seconds, duration_in_traffic_seconds, distance_meters,
                    raw_response_json, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(origin_lat_e5, origin_lng_e5, branch_number, mode, provider)
                   DO UPDATE SET
                     duration_seconds = excluded.duration_seconds,
                     duration_in_traffic_seconds = excluded.duration_in_traffic_seconds,
                     distance_meters = excluded.distance_meters,
                     raw_response_json = excluded.raw_response_json,
                     fetched_at = CURRENT_TIMESTAMP,
                     expires_at = excluded.expires_at""",
                rows,
            )

    def _provider_ttl(self, provider_name: str) -> int:
        row = self.conn.execute(
            "SELECT default_cache_ttl_seconds FROM providers WHERE name = ?",
            (provider_name,),
        ).fetchone()
        return int(row["default_cache_ttl_seconds"]) if row else 86400

    # ------------------------------------------------------------------
    # Main API
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Stage 1 — air-distance shortlist (no routing API call)
    # ------------------------------------------------------------------
    def find_nearest_by_air_distance(
        self,
        origin: Coordinate | str,
        k: int = 50,
        *,
        required_services: list[int] | None = None,
    ) -> list[NearestHit]:
        """Geocode the address (cached) → use the expanding-radius R-Tree ladder
        to fetch candidates → rank top-k by haversine distance.

        NO routing API call. Pure in-memory spatial math. Use this as the
        cheap first stage before any expensive Matrix/Directions request.

        Returns: list of NearestHit (branch + distance_m), nearest first.
        """
        if isinstance(origin, str):
            coord = self.geocode(origin)
            if coord is None:
                return []
        else:
            coord = origin

        return self.index.nearest_haversine(
            coord.lat, coord.lng, k=k,
            required_services=required_services,
        )

    # ------------------------------------------------------------------
    # Internal: matrix-driven top-K ranking with batching + cache + quota.
    # Used by Stage 2 (baseline routing) AND Stage 3 (traffic-aware re-rank).
    # ------------------------------------------------------------------
    def _matrix_rerank(
        self,
        origin: Coordinate,
        candidates,                          # iterable of objects with .branch
        k: int,
        *,
        provider: RoutingProvider,
        mode: str,
        options: RoutingOptions | None,
        ttl_seconds_override: int | None = None,
    ) -> list[RankedBranch]:
        if not candidates:
            return []

        candidate_bns = [c.branch.branch_number for c in candidates]
        bn_to_branch  = {c.branch.branch_number: c.branch for c in candidates}

        cached = self._fetch_cached_legs(origin, candidate_bns, mode, provider.name)
        misses = [bn for bn in candidate_bns if bn not in cached]

        if misses:
            # Serialize concurrent requests hitting the identical
            # (origin, mode, provider) cache namespace so only one caller
            # ever pays the provider for a given miss; the loser re-checks
            # the cache (now populated by the winner) instead of re-calling.
            olat_e5, olng_e5 = self._cache_key(origin.lat, origin.lng)
            lock_key = f"matrix:{provider.name}:{mode}:{olat_e5}:{olng_e5}"
            with self._lock_for_key(lock_key):
                recheck = self._fetch_cached_legs(origin, misses, mode, provider.name)
                cached.update(recheck)
                misses = [bn for bn in misses if bn not in recheck]

                if misses:
                    cap = max(1, int(getattr(provider, "max_destinations_per_matrix_call", 25)))
                    num_batches = (len(misses) + cap - 1) // cap

                    if not self.quota.allow(provider.name, "matrix", units=num_batches):
                        st = self.quota.status(provider.name, "matrix")
                        raise RuntimeError(
                            f"Quota exceeded for {provider.name}/matrix "
                            f"(need {num_batches} units; {st.reason}; retry in {st.retry_after_s:.0f}s). "
                            f"Daily {st.daily_used}/{st.daily_limit}, minute {st.minute_used}/{st.minute_limit}."
                        )

                    ttl = ttl_seconds_override if ttl_seconds_override is not None \
                          else self._provider_ttl(provider.name)

                    for i in range(0, len(misses), cap):
                        batch_bns    = misses[i:i + cap]
                        batch_coords = [Coordinate(bn_to_branch[bn].latitude, bn_to_branch[bn].longitude)
                                        for bn in batch_bns]
                        t0 = time.perf_counter()
                        try:
                            legs = provider.matrix(origin, batch_coords, mode=mode, options=options)
                        except Exception:
                            remaining = num_batches - (i // cap)
                            self.quota.refund(provider.name, "matrix", units=remaining)
                            raise
                        dur_ms = int((time.perf_counter() - t0) * 1000)

                        self._store_legs(origin, batch_bns, legs, mode, provider.name, ttl)
                        with self.conn:
                            self.conn.execute(
                                """INSERT INTO routing_requests_log
                                   (provider, request_type, origin_lat, origin_lng,
                                    num_destinations, mode, status_code, elements_billed, duration_ms)
                                   VALUES (?, 'matrix', ?, ?, ?, ?, 200, ?, ?)""",
                                (provider.name, origin.lat, origin.lng, len(batch_bns),
                                 mode, len(batch_bns), dur_ms),
                            )
                        for bn, leg in zip(batch_bns, legs):
                            if leg is not None:
                                cached[bn] = leg

        def time_key(bn: int) -> int:
            leg = cached.get(bn)
            if leg is None:
                return 10**9
            return leg.duration_in_traffic_seconds or leg.duration_seconds

        miss_set = set(misses)
        ranked_bns = nsmallest(k, candidate_bns, key=time_key)
        return [
            RankedBranch(
                branch=bn_to_branch[bn],
                distance_m=cached[bn].distance_meters,
                duration_seconds=cached[bn].duration_seconds,
                duration_in_traffic_seconds=cached[bn].duration_in_traffic_seconds,
                cache_hit=(bn not in miss_set),
            )
            for bn in ranked_bns
            if bn in cached
        ]

    # ------------------------------------------------------------------
    # Stage 2 — rank a shortlist by drive time (cheap routing provider)
    # ------------------------------------------------------------------
    def rank_by_drive_time(
        self,
        origin: Coordinate,
        candidates,
        k: int = 30,
        *,
        mode: str = "driving",
        options: RoutingOptions | None = None,
    ) -> list[RankedBranch]:
        """Take the shortlist from Stage 1, return top-k by drive time using
        the baseline routing provider. Auto-batches per provider's per-call cap."""
        return self._matrix_rerank(
            origin, candidates, k,
            provider=self.routing, mode=mode, options=options,
        )

    # ------------------------------------------------------------------
    # Stage 3 — re-rank a smaller list with a TRAFFIC-AWARE provider (Google).
    # ------------------------------------------------------------------
    def rerank_with_traffic(
        self,
        origin: Coordinate,
        candidates,
        k: int = 10,
        *,
        traffic_provider: RoutingProvider | None = None,
        mode: str = "driving",
        options: RoutingOptions | None = None,
        ttl_seconds: int = 300,    # 5 min — traffic-aware results decay fast
    ) -> list[RankedBranch]:
        """Re-rank a small shortlist using a different (premium, traffic-aware)
        provider. Use this after rank_by_drive_time has narrowed candidates
        cheaply, so the expensive provider only sees the final-tier handful.

        Cache namespace is keyed on the traffic provider's name → results are
        kept separately from baseline routing.

        TTL defaults to 5 min; override via `ttl_seconds`.
        """
        provider = traffic_provider or self.traffic
        if provider is None:
            raise ValueError(
                "rerank_with_traffic needs a traffic-aware provider. "
                "Pass traffic_provider= or set NearestBranchService(traffic_provider=...)"
            )
        if not getattr(provider, "supports_traffic", False):
            warnings.warn(
                f"{provider.name}: doesn't declare traffic support — proceeding anyway",
                stacklevel=2,
            )

        # Ensure depart_at is set so providers like Google return duration_in_traffic.
        opts = options
        if opts is None:
            opts = RoutingOptions(depart_at=int(time.time()))
        elif opts.depart_at is None:
            from dataclasses import replace
            opts = replace(opts, depart_at=int(time.time()))

        return self._matrix_rerank(
            origin, candidates, k,
            provider=provider, mode=mode, options=opts,
            ttl_seconds_override=ttl_seconds,
        )

    # ------------------------------------------------------------------
    # One-shot convenience: stage 1 + stage 2
    # ------------------------------------------------------------------
    def find_nearest(
        self,
        origin: Coordinate | str,
        k: int = 5,
        *,
        candidate_pool: int = 25,
        mode: str = "driving",
        required_services: list[int] | None = None,
        options: RoutingOptions | None = None,
    ) -> list[RankedBranch]:
        """Address → top-k branches by real travel time.
        Composition of `find_nearest_by_air_distance` + `rank_by_drive_time`.
        """
        if isinstance(origin, str):
            coord = self.geocode(origin)
            if coord is None:
                return []
        else:
            coord = origin

        candidates = self.find_nearest_by_air_distance(
            coord, k=candidate_pool, required_services=required_services,
        )
        if not candidates:
            return []

        return self.rank_by_drive_time(
            coord, candidates, k=k, mode=mode, options=options,
        )

    # ------------------------------------------------------------------
    # 3-tier convenience: address → top-N by live-traffic time
    # ------------------------------------------------------------------
    def find_nearest_with_traffic(
        self,
        origin: Coordinate | str,
        *,
        air_pool:   int = 50,         # Stage 1 — air distance (free)
        drive_pool: int = 15,         # Stage 2 — baseline drive (cheap routing)
        final_k:    int = 10,         # Stage 3 — traffic-aware (premium)
        mode: str = "driving",
        required_services: list[int] | None = None,
        options: RoutingOptions | None = None,
        traffic_provider: RoutingProvider | None = None,
    ) -> list[RankedBranch]:
        """Three-tier pipeline:

        Stage 1 — find_nearest_by_air_distance(k=air_pool)   no API
        Stage 2 — rank_by_drive_time(k=drive_pool)           cheap matrix (e.g. ORS)
        Stage 3 — rerank_with_traffic(k=final_k)             premium (e.g. Google)

        Each stage narrows the candidate set so the expensive Google call only
        sees a handful of branches. With air_pool=50, drive_pool=15, final_k=10
        and Google's 25-dest cap → exactly 1 Google API call per request.
        """
        if isinstance(origin, str):
            coord = self.geocode(origin)
            if coord is None:
                return []
        else:
            coord = origin

        air_hits = self.find_nearest_by_air_distance(
            coord, k=air_pool, required_services=required_services,
        )
        if not air_hits:
            return []

        drive_ranked = self.rank_by_drive_time(
            coord, air_hits, k=drive_pool, mode=mode, options=options,
        )
        if not drive_ranked:
            return []

        return self.rerank_with_traffic(
            coord, drive_ranked, k=final_k,
            mode=mode, options=options, traffic_provider=traffic_provider,
        )

    # ------------------------------------------------------------------
    # find_within_minutes — yes/no reachability
    # Uses isochrone when the provider supports it (1 cheap call), falls back
    # to matrix + duration filter when it doesn't.
    # ------------------------------------------------------------------
    def find_within_minutes(
        self,
        origin: Coordinate | str,
        minutes: float,
        *,
        mode: str = "driving",
        required_services: list[int] | None = None,
        candidate_pool: int = 50,
        options: RoutingOptions | None = None,
    ) -> list[ReachableBranch]:
        """All branches reachable from `origin` within `minutes` minutes.

        Strategy:
          - If the routing provider supports isochrones → 1 isochrone call,
            then in-memory point-in-polygon filter.  No per-branch routing.
          - Otherwise → spatial pre-filter + matrix call + duration threshold.

        Returns a list sorted by haversine distance to origin.
        """
        if isinstance(origin, str):
            coord = self.geocode(origin)
            if coord is None:
                return []
        else:
            coord = origin

        seconds = int(minutes * 60)

        # ---- Path 1: provider can do isochrone — cheaper ----
        if getattr(self.routing, "supports_isochrone", False):
            if not self.quota.allow(self.routing.name, "isochrones", units=1):
                st = self.quota.status(self.routing.name, "isochrones")
                raise RuntimeError(
                    f"Isochrones quota exceeded for {self.routing.name} "
                    f"({st.reason}; retry in {st.retry_after_s:.0f}s)."
                )
            t0 = time.perf_counter()
            try:
                bands = self.routing.isochrone(coord, [seconds], mode=mode, options=options)
            except Exception:
                self.quota.refund(self.routing.name, "isochrones", units=1)
                raise
            dur_ms = int((time.perf_counter() - t0) * 1000)

            with self.conn:
                self.conn.execute(
                    """INSERT INTO routing_requests_log
                       (provider, request_type, origin_lat, origin_lng,
                        num_destinations, mode, status_code, elements_billed, duration_ms)
                       VALUES (?, 'isochrones', ?, ?, 1, ?, 200, 1, ?)""",
                    (self.routing.name, coord.lat, coord.lng, mode, dur_ms),
                )

            if not bands:
                return []
            polygon = bands[0].geometry_geojson or {}

            # Spatial pre-filter via R-Tree on the bounding box of the polygon.
            # Handle Polygon vs MultiPolygon explicitly and bail out to []
            # for missing/empty/degenerate geometry (e.g. an
            # OpenRouteServiceProvider isochrone that returned {} on a
            # degenerate response) instead of indexing into a nested default.
            gtype = polygon.get("type")
            coords = polygon.get("coordinates") or []
            if gtype == "Polygon":
                ring = coords[0] if coords else []
            elif gtype == "MultiPolygon":
                first_poly = coords[0] if coords else []
                ring = first_poly[0] if first_poly else []
            else:
                ring = []

            if not ring:
                return []

            lats = [p[1] for p in ring]
            lngs = [p[0] for p in ring]
            cur = self.conn.cursor()
            bn_candidates = [r[0] for r in cur.execute(
                "SELECT branch_number FROM branches_rtree "
                "WHERE max_lat >= ? AND min_lat <= ? AND max_lng >= ? AND min_lng <= ?",
                (min(lats), max(lats), min(lngs), max(lngs)),
            )]

            # Filter by required services
            if required_services:
                svc_filter = self.index.with_all_services(required_services)
                bn_candidates = [bn for bn in bn_candidates if bn in svc_filter]

            # Point-in-polygon refine
            results: list[ReachableBranch] = []
            for bn in bn_candidates:
                b = self.index.by_number[bn]
                if not point_in_polygon(b.longitude, b.latitude, polygon):
                    continue
                results.append(ReachableBranch(
                    branch=b,
                    range_seconds=seconds,
                    haversine_m=int(haversine_m(coord.lng, coord.lat, b.longitude, b.latitude)),
                ))
            results.sort(key=lambda r: r.haversine_m)
            return results

        # ---- Path 2: matrix fallback ----
        ranked = self.find_nearest(
            coord, k=candidate_pool, candidate_pool=candidate_pool,
            mode=mode, required_services=required_services, options=options,
        )
        out: list[ReachableBranch] = []
        for r in ranked:
            chosen = r.duration_in_traffic_seconds or r.duration_seconds
            if chosen <= seconds:
                out.append(ReachableBranch(
                    branch=r.branch,
                    range_seconds=seconds,
                    haversine_m=int(haversine_m(coord.lng, coord.lat, r.branch.longitude, r.branch.latitude)),
                ))
        out.sort(key=lambda r: r.haversine_m)
        return out

    # ------------------------------------------------------------------
    def close(self) -> None:
        self.conn.close()


# ----------------------------------------------------------------------------
# CLI demo with the mock provider
# ----------------------------------------------------------------------------
def _demo() -> None:
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from providers import MockHaversineProvider

    svc = NearestBranchService(routing_provider=MockHaversineProvider(avg_kmh=50))

    print(f"Index loaded: {svc.index.stats()}")
    print()

    test_origins = [
        ("Tel Aviv – Dizengoff", Coordinate(32.0809, 34.7741)),
        ("Jerusalem – Mahane Yehuda", Coordinate(31.7857, 35.2118)),
        ("Eilat – center",  Coordinate(29.5577, 34.9519)),
        ("Negev far rural", Coordinate(30.6, 34.7)),
    ]

    print("--- find_nearest (k=5 by travel time) ---")
    for label, origin in test_origins:
        print(f"\n=== {label}  ({origin.lat:.4f}, {origin.lng:.4f}) ===")
        t0 = time.perf_counter()
        results = svc.find_nearest(origin, k=5, candidate_pool=25)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        print(f"  -> {elapsed_ms:.2f} ms total")
        for i, r in enumerate(results, 1):
            mins = r.duration_seconds / 60
            km = r.distance_m / 1000
            tag = "[cache]" if r.cache_hit else "[live] "
            print(f"  {i}. {tag} #{r.branch.branch_number:<5} {r.branch.branch_name:<30} "
                  f"{km:5.2f} km  ~{mins:5.1f} min")

    print("\n--- find_within_minutes (isochrone path with mock provider) ---")
    for label, origin in test_origins[:2]:
        print(f"\n=== {label} — within 5 min ===")
        t0 = time.perf_counter()
        within = svc.find_within_minutes(origin, minutes=5)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        print(f"  -> {elapsed_ms:.2f} ms, {len(within)} branches reachable")
        for r in within[:5]:
            print(f"    #{r.branch.branch_number:<5} {r.branch.branch_name:<30} "
                  f"{r.haversine_m/1000:5.2f} km away")

    svc.close()


if __name__ == "__main__":
    _demo()
