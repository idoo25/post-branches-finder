"""Geocoding providers — Nominatim, ORS Pelias, Chained."""
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from io import BytesIO

from . import _helpers
from ._helpers import (FakeGeocoder, NoneGeocoder, captured_request,
                       fake_urlopen_response)
from providers import (ChainedGeocodingProvider, GeocodeResult,
                       NominatimProvider, OpenRouteServiceGeocodingProvider)


class TestORSGeocode(unittest.TestCase):
    def setUp(self):
        self.p = OpenRouteServiceGeocodingProvider(api_key="K")

    @patch("urllib.request.urlopen")
    def test_geocode_request_url(self, mu):
        mu.return_value = fake_urlopen_response({
            "features": [{
                "geometry": {"coordinates": [34.7741, 32.0809]},
                "properties": {"label": "Dizengoff 50, Tel Aviv"},
            }]
        })
        r = self.p.geocode("Dizengoff 50, Tel Aviv")
        req, _ = captured_request(mu)
        self.assertIn("/geocode/search?", req.full_url)
        self.assertIn("text=", req.full_url)
        self.assertIn("boundary.country=ISR", req.full_url)
        self.assertEqual(req.headers["Authorization"], "K")
        self.assertEqual(r.lat, 32.0809)
        self.assertEqual(r.lng, 34.7741)
        self.assertEqual(r.formatted_address, "Dizengoff 50, Tel Aviv")

    @patch("urllib.request.urlopen")
    def test_geocode_empty_features_returns_none(self, mu):
        mu.return_value = fake_urlopen_response({"features": []})
        self.assertIsNone(self.p.geocode("Nowhere"))

    @patch("urllib.request.urlopen")
    def test_autocomplete_uses_correct_endpoint(self, mu):
        mu.return_value = fake_urlopen_response({
            "features": [
                {"geometry": {"coordinates": [34.77, 32.08]},
                 "properties": {"label": "Tel Aviv, Israel"}},
                {"geometry": {"coordinates": [35.21, 31.78]},
                 "properties": {"label": "Jerusalem, Israel"}},
            ]
        })
        results = self.p.autocomplete("Tel A")
        req, _ = captured_request(mu)
        self.assertIn("/geocode/autocomplete?", req.full_url)
        self.assertEqual(len(results), 2)

    def test_autocomplete_short_query_returns_empty(self):
        # No HTTP call should be made for queries shorter than 2 chars
        with patch("urllib.request.urlopen") as mu:
            r = self.p.autocomplete("a")
            self.assertEqual(r, [])
            mu.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_http_error_propagates(self, mu):
        mu.side_effect = HTTPError("u", 401, "Unauthorized", {}, BytesIO(b'{"error":"bad key"}'))
        with self.assertRaisesRegex(RuntimeError, "401"):
            self.p.geocode("Tel Aviv")


class TestNominatim(unittest.TestCase):
    def setUp(self):
        self.p = NominatimProvider(user_agent="post-branches-test/1.0")
        # reset rate-limit timer so the tests don't sleep
        NominatimProvider._last_call_ts = 0.0

    @patch("urllib.request.urlopen")
    def test_request_includes_country_code_and_user_agent(self, mu):
        mu.return_value = fake_urlopen_response([{
            "lat": "32.0788", "lon": "34.7741",
            "display_name": "Dizengoff Center, Tel Aviv-Yafo",
            # A house-number query (address contains a digit) is only accepted
            # when Nominatim's match includes a road-level component — without
            # it, geocode() correctly rejects a city-centroid mismatch as None.
            "address": {"road": "Dizengoff Street", "city": "Tel Aviv-Yafo",
                        "country": "Israel"},
        }])
        r = self.p.geocode("Dizengoff 50")
        req, _ = captured_request(mu)
        self.assertIn("nominatim.openstreetmap.org/search", req.full_url)
        self.assertIn("countrycodes=il", req.full_url)
        self.assertEqual(req.headers["User-agent"], "post-branches-test/1.0")
        self.assertEqual(r.lat, 32.0788)
        self.assertEqual(r.lng, 34.7741)

    @patch("urllib.request.urlopen")
    def test_empty_results_returns_none(self, mu):
        mu.return_value = fake_urlopen_response([])
        self.assertIsNone(self.p.geocode("Nowhere place"))

    @patch("urllib.request.urlopen")
    @patch("time.sleep")
    def test_rate_limit_one_per_second(self, mock_sleep, mu):
        mu.return_value = fake_urlopen_response([{"lat":"32","lon":"34","display_name":""}])
        # First call: no sleep needed (timestamp is 0)
        self.p.geocode("a place")
        # Second call: should sleep ~1 sec
        self.p.geocode("another place")
        # at least one sleep happened with positive duration
        sleeps = [c.args[0] for c in mock_sleep.call_args_list if c.args]
        self.assertTrue(any(s > 0 for s in sleeps),
                        f"expected positive sleep, got {sleeps}")


class TestChainedGeocoder(unittest.TestCase):
    def test_returns_first_success(self):
        a = FakeGeocoder(name="a", lat=10, lng=20)
        b = FakeGeocoder(name="b", lat=99, lng=99)
        chain = ChainedGeocodingProvider([a, b])
        r = chain.geocode("addr")
        self.assertEqual((r.lat, r.lng), (10, 20))
        self.assertEqual(a.calls, ["addr"])
        self.assertEqual(b.calls, [])  # b never reached

    def test_falls_through_when_first_returns_none(self):
        a = NoneGeocoder()
        b = FakeGeocoder(name="b", lat=42, lng=43)
        chain = ChainedGeocodingProvider([a, b])
        r = chain.geocode("addr")
        self.assertEqual((r.lat, r.lng), (42, 43))
        self.assertEqual(a.calls, ["addr"])
        self.assertEqual(b.calls, ["addr"])

    def test_falls_through_when_first_throws(self):
        class Boom:
            name = "boom"
            def geocode(self, address):
                raise RuntimeError("oh no")
        b = FakeGeocoder(name="b", lat=42, lng=43)
        chain = ChainedGeocodingProvider([Boom(), b])
        r = chain.geocode("addr")
        self.assertEqual(r.lat, 42)

    def test_re_raises_when_all_throw(self):
        class Boom:
            name = "x"
            def geocode(self, address):
                raise RuntimeError("oh no")
        chain = ChainedGeocodingProvider([Boom(), Boom()])
        with self.assertRaises(RuntimeError):
            chain.geocode("addr")

    def test_returns_none_when_all_return_none(self):
        chain = ChainedGeocodingProvider([NoneGeocoder(), NoneGeocoder()])
        self.assertIsNone(chain.geocode("addr"))

    def test_autocomplete_delegated_to_first_supporting(self):
        b_with_auto = OpenRouteServiceGeocodingProvider(api_key="K")
        # Wrap a Nominatim-style stub (no autocomplete) followed by ORS
        chain = ChainedGeocodingProvider([NoneGeocoder(), b_with_auto])
        with patch("urllib.request.urlopen") as mu:
            mu.return_value = fake_urlopen_response({"features":[]})
            chain.autocomplete("Tel A", size=3)
            mu.assert_called_once()

    def test_autocomplete_returns_empty_when_no_provider_supports(self):
        chain = ChainedGeocodingProvider([NoneGeocoder()])
        self.assertEqual(chain.autocomplete("Tel A"), [])

    def test_explicit_name_overrides_default(self):
        a = FakeGeocoder(name="a")
        chain = ChainedGeocodingProvider([a], name="my-chain")
        self.assertEqual(chain.name, "my-chain")

    def test_default_name_is_first_provider(self):
        a = FakeGeocoder(name="alpha")
        chain = ChainedGeocodingProvider([a])
        self.assertEqual(chain.name, "alpha")

    def test_empty_providers_raises(self):
        with self.assertRaises(ValueError):
            ChainedGeocodingProvider([])


if __name__ == "__main__":
    unittest.main()
