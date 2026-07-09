"""
Quota guard for outbound API calls.

Enforces per-provider × per-endpoint limits on:
    - daily quota   (e.g. 3000/day for openrouteservice geocoding)
    - per-minute rate (e.g. 100/min)

Usage in hot path:
    if not guard.allow("openrouteservice", "matrix", units=1):
        # falls back to another provider, queues, or returns an error
        ...
    # ... do the call ...
    guard.record(...)   # automatic via routing_requests_log

The guard reads usage from `routing_requests_log` over a sliding window — no
separate counter to drift out of sync. For the per-minute check we keep a tiny
in-process token bucket per (provider, endpoint) so the hot path never hits
the DB unless the bucket says we *might* be over.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass(slots=True)
class QuotaStatus:
    allowed:        bool
    reason:         str | None       # 'daily' | 'per_minute' | None
    daily_used:     int
    daily_limit:    int | None
    minute_used:    int
    minute_limit:   int | None
    retry_after_s:  float = 0.0


class QuotaGuard:
    """One instance per process. Thread-unsafe — wrap with a Lock if needed."""

    __slots__ = ("conn", "_policy", "_minute_buckets")

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._policy: dict[tuple[str, str], tuple[int | None, int | None]] = {}
        # bucket = (window_start_ts, count). Reset every 60 s.
        self._minute_buckets: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0])
        self._reload_policy()

    def _reload_policy(self) -> None:
        self._policy.clear()
        for row in self.conn.execute(
            "SELECT provider_name, endpoint, daily_limit, per_minute_limit FROM provider_quotas"
        ):
            self._policy[(row[0], row[1])] = (row[2], row[3])

    # ------------------------------------------------------------------
    def status(self, provider: str, endpoint: str) -> QuotaStatus:
        daily_limit, minute_limit = self._policy.get((provider, endpoint), (None, None))

        # Daily — sliding window over 24h.
        if daily_limit is not None:
            daily_used = self.conn.execute(
                """SELECT COALESCE(SUM(elements_billed), COUNT(*)) FROM routing_requests_log
                   WHERE provider = ? AND request_type = ?
                     AND requested_at > datetime('now','-1 day')""",
                (provider, endpoint),
            ).fetchone()[0] or 0
        else:
            daily_used = 0

        # Per-minute — fast in-memory bucket; refill every 60 s.
        bucket = self._minute_buckets[(provider, endpoint)]
        now = time.monotonic()
        if now - bucket[0] >= 60.0:
            bucket[0], bucket[1] = now, 0
        minute_used = bucket[1]

        # Decide
        if daily_limit is not None and daily_used >= daily_limit:
            return QuotaStatus(False, "daily", daily_used, daily_limit, minute_used, minute_limit,
                               retry_after_s=24 * 3600)
        if minute_limit is not None and minute_used >= minute_limit:
            wait = max(0.0, 60.0 - (now - bucket[0]))
            return QuotaStatus(False, "per_minute", daily_used, daily_limit, minute_used, minute_limit,
                               retry_after_s=wait)
        return QuotaStatus(True, None, daily_used, daily_limit, minute_used, minute_limit)

    # ------------------------------------------------------------------
    def allow(self, provider: str, endpoint: str, units: int = 1) -> bool:
        """Check + reserve. Returns False if the call would exceed quota."""
        st = self.status(provider, endpoint)
        if not st.allowed:
            return False
        # Pre-reserve in the per-minute bucket so concurrent callers see it.
        bucket = self._minute_buckets[(provider, endpoint)]
        bucket[1] += units
        return True

    def refund(self, provider: str, endpoint: str, units: int = 1) -> None:
        """Roll back a reservation if the call ultimately failed before sending."""
        bucket = self._minute_buckets[(provider, endpoint)]
        bucket[1] = max(0, bucket[1] - units)

    # ------------------------------------------------------------------
    def remaining(self, provider: str, endpoint: str) -> dict:
        st = self.status(provider, endpoint)
        return {
            "daily_remaining":  None if st.daily_limit  is None else max(0, st.daily_limit  - st.daily_used),
            "minute_remaining": None if st.minute_limit is None else max(0, st.minute_limit - st.minute_used),
            "daily_limit":      st.daily_limit,
            "minute_limit":     st.minute_limit,
        }
