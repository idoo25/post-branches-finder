"""QuotaGuard — sliding-window daily and per-minute limits."""
import sqlite3
import unittest
from unittest.mock import patch

from . import _helpers
from ._helpers import build_test_db
from quota import QuotaGuard


class TestQuotaGuard(unittest.TestCase):
    def setUp(self):
        self.conn = build_test_db()

    def tearDown(self):
        self.conn.close()

    def _set_limit(self, provider, endpoint, daily=None, per_minute=None):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO provider_quotas(provider_name, endpoint, daily_limit, per_minute_limit) "
                "VALUES (?, ?, ?, ?)", (provider, endpoint, daily, per_minute))

    def test_unbounded_provider_always_allows(self):
        # No provider_quotas row → no limit
        g = QuotaGuard(self.conn)
        for _ in range(10):
            self.assertTrue(g.allow("nonexistent_provider", "matrix"))

    def test_daily_blocks_when_exhausted(self):
        self._set_limit("here_matrix", "matrix", daily=2, per_minute=10)
        g = QuotaGuard(self.conn)
        # Manually log 2 prior calls today
        with self.conn:
            self.conn.execute(
                "INSERT INTO routing_requests_log(provider, request_type, elements_billed) "
                "VALUES ('here_matrix','matrix',1),('here_matrix','matrix',1)")
        st = g.status("here_matrix", "matrix")
        self.assertFalse(st.allowed)
        self.assertEqual(st.reason, "daily")
        self.assertEqual(st.daily_used, 2)

    def test_per_minute_blocks_after_burst(self):
        self._set_limit("here_matrix", "matrix", daily=None, per_minute=2)
        g = QuotaGuard(self.conn)
        self.assertTrue(g.allow("here_matrix", "matrix"))
        self.assertTrue(g.allow("here_matrix", "matrix"))
        self.assertFalse(g.allow("here_matrix", "matrix"))
        st = g.status("here_matrix", "matrix")
        self.assertFalse(st.allowed)
        self.assertEqual(st.reason, "per_minute")

    def test_refund_restores_minute_bucket(self):
        self._set_limit("here_matrix", "matrix", per_minute=2)
        g = QuotaGuard(self.conn)
        g.allow("here_matrix", "matrix", units=2)
        self.assertFalse(g.allow("here_matrix", "matrix"))
        g.refund("here_matrix", "matrix", units=2)
        self.assertTrue(g.allow("here_matrix", "matrix"))

    def test_remaining_helper(self):
        self._set_limit("here_matrix", "matrix", daily=10, per_minute=5)
        g = QuotaGuard(self.conn)
        rem = g.remaining("here_matrix", "matrix")
        self.assertEqual(rem["daily_limit"], 10)
        self.assertEqual(rem["minute_limit"], 5)

    def test_units_argument_consumes_multiple(self):
        self._set_limit("here_matrix", "matrix", per_minute=3)
        g = QuotaGuard(self.conn)
        self.assertTrue(g.allow("here_matrix", "matrix", units=2))
        self.assertTrue(g.allow("here_matrix", "matrix", units=1))
        self.assertFalse(g.allow("here_matrix", "matrix"))


if __name__ == "__main__":
    unittest.main()
