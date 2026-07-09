"""Routing providers — request shape, response parsing, error handling.

Every test mocks `urllib.request.urlopen` so no network call ever fires.
"""
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from . import _helpers
from ._helpers import captured_request, fake_urlopen_response

from providers import (Coordinate, GoogleDistanceMatrixProvider,
                       HEREMatrixProvider, MockHaversineProvider,
                       OpenRouteServiceProvider, RoutingOptions, TravelLeg,
                       pick_provider)


# ===========================================================================
# OpenRouteService — Matrix
# ===========================================================================
class TestORSMatrix(unittest.TestCase):
    def setUp(self):
        self.p = OpenRouteServiceProvider(api_key="TEST_KEY")
        self.origin = Coordinate(32.0, 34.7)
        self.dests  = [Coordinate(32.1, 34.8), Coordinate(32.2, 34.9)]

    @patch("urllib.request.urlopen")
    def test_request_uses_lng_lat_order(self, mu):
        mu.return_value = fake_urlopen_response({
            "durations": [[120.5, 240.7]],
            "distances": [[1000, 2000]],
        })
        self.p.matrix(self.origin, self.dests)
        req, body = captured_request(mu)
        # critical: GeoJSON convention is [lng, lat]
        self.assertEqual(body["locations"], [[34.7, 32.0], [34.8, 32.1], [34.9, 32.2]])
        self.assertEqual(body["sources"], [0])
        self.assertEqual(body["destinations"], [1, 2])
        self.assertEqual(body["metrics"], ["duration", "distance"])

    @patch("urllib.request.urlopen")
    def test_url_includes_profile(self, mu):
        mu.return_value = fake_urlopen_response({"durations":[[60]],"distances":[[500]]})
        self.p.matrix(self.origin, self.dests[:1], mode="walking")
        req, _ = captured_request(mu)
        self.assertIn("/v2/matrix/foot-walking", req.full_url)

    @patch("urllib.request.urlopen")
    def test_authorization_header(self, mu):
        mu.return_value = fake_urlopen_response({"durations":[[60]],"distances":[[500]]})
        self.p.matrix(self.origin, self.dests[:1])
        req, _ = captured_request(mu)
        self.assertEqual(req.headers["Authorization"], "TEST_KEY")
        self.assertEqual(req.method, "POST")

    @patch("urllib.request.urlopen")
    def test_response_parsed_to_travel_legs(self, mu):
        mu.return_value = fake_urlopen_response({
            "durations": [[120.5, 240.7]],
            "distances": [[1000, 2000]],
        })
        legs = self.p.matrix(self.origin, self.dests)
        self.assertEqual(len(legs), 2)
        self.assertIsInstance(legs[0], TravelLeg)
        self.assertEqual(legs[0].duration_seconds, 120)
        self.assertEqual(legs[0].distance_meters, 1000)
        self.assertIsNone(legs[0].duration_in_traffic_seconds)

    @patch("urllib.request.urlopen")
    def test_unreachable_returns_none(self, mu):
        mu.return_value = fake_urlopen_response({
            "durations": [[None, 240.7]],
            "distances": [[None, 2000]],
        })
        legs = self.p.matrix(self.origin, self.dests)
        self.assertIsNone(legs[0])
        self.assertIsNotNone(legs[1])

    @patch("urllib.request.urlopen")
    def test_http_error_propagates_with_message(self, mu):
        mu.side_effect = HTTPError("u", 401, "Unauthorized", {}, _make_fp(b'{"error":"bad key"}'))
        with self.assertRaises(RuntimeError) as ctx:
            self.p.matrix(self.origin, self.dests)
        self.assertIn("401", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_unknown_mode_raises(self, mu):
        with self.assertRaises(ValueError):
            self.p.matrix(self.origin, self.dests, mode="rocket")
        mu.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_options_warns_on_avoid_polygons(self, mu):
        mu.return_value = fake_urlopen_response({"durations":[[60]],"distances":[[500]]})
        opts = RoutingOptions(avoid_polygons={"type":"Polygon","coordinates":[[]]})
        with self.assertWarnsRegex(UserWarning, "avoid_polygons"):
            self.p.matrix(self.origin, self.dests[:1], options=opts)


def _make_fp(b):
    """Build a file-like for HTTPError(.read())."""
    from io import BytesIO
    return BytesIO(b)


# ===========================================================================
# OpenRouteService — Isochrone + Route
# ===========================================================================
class TestORSIsochroneAndRoute(unittest.TestCase):
    def setUp(self):
        self.p = OpenRouteServiceProvider(api_key="K")

    @patch("urllib.request.urlopen")
    def test_isochrone_request_shape(self, mu):
        mu.return_value = fake_urlopen_response({
            "type": "FeatureCollection",
            "features": [{
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
                "properties": {"value": 600, "area": 12345.6}
            }],
        })
        result = self.p.isochrone(Coordinate(32.0, 34.7), [600], mode="driving")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].range_seconds, 600)
        req, body = captured_request(mu)
        self.assertEqual(body["locations"], [[34.7, 32.0]])
        self.assertEqual(body["range"], [600])
        self.assertIn("/v2/isochrones/driving-car", req.full_url)

    @patch("urllib.request.urlopen")
    def test_isochrone_passes_avoid_polygons(self, mu):
        mu.return_value = fake_urlopen_response({"features": []})
        opts = RoutingOptions(avoid_polygons={"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1]]]})
        self.p.isochrone(Coordinate(32, 34.7), [600], options=opts)
        _, body = captured_request(mu)
        self.assertIn("options", body)
        self.assertIn("avoid_polygons", body["options"])

    @patch("urllib.request.urlopen")
    def test_route_returns_leg(self, mu):
        mu.return_value = fake_urlopen_response({
            "routes": [{
                "summary": {"duration": 480, "distance": 7500},
                "geometry": "encoded_polyline_here",
            }]
        })
        leg = self.p.route(Coordinate(32, 34.7), Coordinate(32.5, 35), mode="driving")
        self.assertEqual(leg.duration_seconds, 480)
        self.assertEqual(leg.distance_meters, 7500)
        self.assertEqual(leg.geometry_encoded, "encoded_polyline_here")


# ===========================================================================
# HERE Matrix — the new provider
# ===========================================================================
class TestHEREMatrix(unittest.TestCase):
    def setUp(self):
        self.p = HEREMatrixProvider(api_key="HERE_KEY_TEST")
        self.origin = Coordinate(32.0853, 34.7818)
        # 15 destinations like the real-world target: 1×15 = 1 call
        self.dests = [Coordinate(32 + i * 0.01, 34.78 + i * 0.005) for i in range(15)]

    @patch("urllib.request.urlopen")
    def test_url_query_param_apikey(self, mu):
        mu.return_value = fake_urlopen_response({
            "matrix": {"travelTimes": [60]*15, "distances": [1000]*15, "errorCodes": [0]*15}
        })
        self.p.matrix(self.origin, self.dests)
        req, _ = captured_request(mu)
        self.assertIn("apiKey=HERE_KEY_TEST", req.full_url)
        self.assertIn("async=false", req.full_url)
        self.assertIn("/v8/matrix", req.full_url)
        self.assertEqual(req.method, "POST")

    @patch("urllib.request.urlopen")
    def test_body_has_lat_lng_keys(self, mu):
        mu.return_value = fake_urlopen_response({
            "matrix": {"travelTimes": [60]*15, "distances": [1000]*15, "errorCodes": [0]*15}
        })
        self.p.matrix(self.origin, self.dests)
        _, body = captured_request(mu)
        # HERE wants {"lat":..., "lng":...} (NOT "lon")
        self.assertEqual(body["origins"], [{"lat": 32.0853, "lng": 34.7818}])
        self.assertEqual(len(body["destinations"]), 15)
        for d in body["destinations"]:
            self.assertIn("lat", d)
            self.assertIn("lng", d)
            self.assertNotIn("lon", d)
        self.assertEqual(body["transportMode"], "car")
        self.assertEqual(body["routingMode"], "fast")
        self.assertEqual(body["matrixAttributes"], ["travelTimes", "distances"])

    @patch("urllib.request.urlopen")
    def test_default_region_is_traffic_eligible(self, mu):
        # default region must be one of: autoCircle / circle / boundingBox /
        # polygon — anything except "world" — so live traffic is enabled.
        mu.return_value = fake_urlopen_response({
            "matrix": {"travelTimes": [60], "distances": [1000], "errorCodes": [0]}
        })
        self.p.matrix(self.origin, self.dests[:1])
        _, body = captured_request(mu)
        rd = body["regionDefinition"]
        self.assertIn(rd["type"], ("autoCircle", "circle", "boundingBox", "polygon"))
        self.assertNotEqual(rd["type"], "world")

    @patch("urllib.request.urlopen")
    def test_explicit_bounding_box_region(self, mu):
        mu.return_value = fake_urlopen_response({
            "matrix": {"travelTimes": [60], "distances": [1000], "errorCodes": [0]}
        })
        from providers import HEREMatrixProvider
        custom = HEREMatrixProvider(api_key="K",
                                     region_definition=HEREMatrixProvider.ISRAEL_BBOX)
        custom.matrix(self.origin, self.dests[:1])
        _, body = captured_request(mu)
        self.assertEqual(body["regionDefinition"]["type"], "boundingBox")

    @patch("urllib.request.urlopen")
    def test_response_row_major_layout_with_one_origin(self, mu):
        # 1 origin × 3 destinations = flat array length 3
        mu.return_value = fake_urlopen_response({
            "matrix": {
                "numOrigins": 1, "numDestinations": 3,
                "travelTimes": [120, 240, 360],
                "distances":   [1000, 2000, 3000],
                "errorCodes":  [0, 0, 0],
            }
        })
        legs = self.p.matrix(self.origin, self.dests[:3])
        self.assertEqual(len(legs), 3)
        for i, leg in enumerate(legs):
            expected_dur = (i + 1) * 120
            self.assertEqual(leg.duration_seconds, expected_dur)
            self.assertEqual(leg.duration_in_traffic_seconds, expected_dur)
            self.assertEqual(leg.distance_meters, (i + 1) * 1000)

    @patch("urllib.request.urlopen")
    def test_unreachable_returns_none_when_error_code_nonzero(self, mu):
        mu.return_value = fake_urlopen_response({
            "matrix": {
                "travelTimes": [120, 0,   240],
                "distances":   [1000, 0,  2000],
                "errorCodes":  [0,   3,   0],   # 3 = options violated (no route)
            }
        })
        legs = self.p.matrix(self.origin, self.dests[:3])
        self.assertIsNotNone(legs[0])
        self.assertIsNone(legs[1])
        self.assertIsNotNone(legs[2])

    @patch("urllib.request.urlopen")
    def test_departure_time_iso8601_when_provided(self, mu):
        mu.return_value = fake_urlopen_response({
            "matrix": {"travelTimes": [60], "distances": [1000], "errorCodes": [0]}
        })
        opts = RoutingOptions(depart_at=1700000000)
        self.p.matrix(self.origin, self.dests[:1], options=opts)
        _, body = captured_request(mu)
        self.assertIn("departureTime", body)
        self.assertTrue(body["departureTime"].startswith("2023-"))
        self.assertIn("T", body["departureTime"])

    @patch("urllib.request.urlopen")
    def test_avoid_features_translation(self, mu):
        mu.return_value = fake_urlopen_response({
            "matrix": {"travelTimes": [60], "distances": [1000], "errorCodes": [0]}
        })
        opts = RoutingOptions(avoid_features=["tolls", "highways"])
        self.p.matrix(self.origin, self.dests[:1], options=opts)
        _, body = captured_request(mu)
        self.assertEqual(set(body["avoid"]["features"]),
                         {"tollRoad", "controlledAccessHighway"})

    @patch("urllib.request.urlopen")
    def test_truck_mode(self, mu):
        mu.return_value = fake_urlopen_response({
            "matrix": {"travelTimes": [60], "distances": [1000], "errorCodes": [0]}
        })
        self.p.matrix(self.origin, self.dests[:1], mode="driving-hgv")
        _, body = captured_request(mu)
        self.assertEqual(body["transportMode"], "truck")

    @patch("urllib.request.urlopen")
    def test_http_error_includes_body(self, mu):
        mu.side_effect = HTTPError("u", 429, "Too Many",
                                   {}, _make_fp(b'{"title":"rate limited"}'))
        with self.assertRaisesRegex(RuntimeError, "HERE Matrix HTTP 429"):
            self.p.matrix(self.origin, self.dests[:1])

    def test_max_destinations_per_call_capacity(self):
        # 1×15 must be in a single batch — that's the user's contract
        self.assertGreaterEqual(self.p.max_destinations_per_matrix_call, 15)

    def test_capability_flags(self):
        self.assertTrue(self.p.supports_matrix)
        self.assertTrue(self.p.supports_traffic)
        self.assertTrue(self.p.supports_avoid_features)
        self.assertFalse(self.p.supports_avoid_polygons)


# ===========================================================================
# Google Distance Matrix
# ===========================================================================
class TestGoogleMatrix(unittest.TestCase):
    def setUp(self):
        self.p = GoogleDistanceMatrixProvider(api_key="GKEY")

    @patch("urllib.request.urlopen")
    def test_url_and_params(self, mu):
        mu.return_value = fake_urlopen_response({
            "status": "OK",
            "rows": [{"elements": [
                {"status": "OK",
                 "distance": {"value": 1234},
                 "duration": {"value": 567},
                 "duration_in_traffic": {"value": 678}}
            ]}],
        })
        self.p.matrix(Coordinate(32, 34), [Coordinate(33, 35)])
        req, _ = captured_request(mu)
        self.assertIn("origins=32%2C34", req.full_url)
        self.assertIn("destinations=33%2C35", req.full_url)
        self.assertIn("departure_time=now", req.full_url)
        self.assertIn("traffic_model=best_guess", req.full_url)
        self.assertIn("key=GKEY", req.full_url)

    @patch("urllib.request.urlopen")
    def test_response_includes_traffic(self, mu):
        mu.return_value = fake_urlopen_response({
            "status": "OK",
            "rows": [{"elements": [
                {"status": "OK",
                 "distance": {"value": 1234},
                 "duration": {"value": 567},
                 "duration_in_traffic": {"value": 678}}
            ]}],
        })
        legs = self.p.matrix(Coordinate(32, 34), [Coordinate(33, 35)])
        self.assertEqual(legs[0].duration_seconds, 567)
        self.assertEqual(legs[0].duration_in_traffic_seconds, 678)

    @patch("urllib.request.urlopen")
    def test_unreachable_element_returns_none(self, mu):
        mu.return_value = fake_urlopen_response({
            "status": "OK",
            "rows": [{"elements": [{"status": "ZERO_RESULTS"}]}],
        })
        legs = self.p.matrix(Coordinate(32, 34), [Coordinate(33, 35)])
        self.assertIsNone(legs[0])

    @patch("urllib.request.urlopen")
    def test_top_level_error_raises(self, mu):
        mu.return_value = fake_urlopen_response({
            "status": "REQUEST_DENIED",
            "error_message": "API key invalid",
        })
        with self.assertRaisesRegex(RuntimeError, "Google DM error.*REQUEST_DENIED"):
            self.p.matrix(Coordinate(32, 34), [Coordinate(33, 35)])

    @patch("urllib.request.urlopen")
    def test_avoid_features_translated(self, mu):
        mu.return_value = fake_urlopen_response({
            "status": "OK", "rows":[{"elements":[{"status":"OK",
            "distance":{"value":0},"duration":{"value":0}}]}]
        })
        opts = RoutingOptions(avoid_features=["tolls", "ferries"])
        self.p.matrix(Coordinate(32, 34), [Coordinate(33, 35)], options=opts)
        req, _ = captured_request(mu)
        self.assertIn("avoid=tolls%7Cferries", req.full_url)


# ===========================================================================
# Mock Haversine — fake provider for tests
# ===========================================================================
class TestMockProvider(unittest.TestCase):
    def test_default_no_traffic(self):
        p = MockHaversineProvider(avg_kmh=50)
        self.assertFalse(p.supports_traffic)
        legs = p.matrix(Coordinate(32, 34), [Coordinate(32.1, 34.1)])
        self.assertEqual(len(legs), 1)
        self.assertIsNone(legs[0].duration_in_traffic_seconds)

    def test_traffic_multiplier_enables_traffic_flag(self):
        p = MockHaversineProvider(avg_kmh=50, traffic_multiplier=1.5)
        self.assertTrue(p.supports_traffic)
        self.assertEqual(p.name, "mock_traffic")
        legs = p.matrix(Coordinate(32, 34), [Coordinate(32.1, 34.1)])
        self.assertIsNotNone(legs[0].duration_in_traffic_seconds)
        # traffic time should be > baseline (multiplier 1.5 with jitter ±15%)
        self.assertGreater(legs[0].duration_in_traffic_seconds,
                           int(legs[0].duration_seconds * 1.0))


# ===========================================================================
# pick_provider — capability-based selection
# ===========================================================================
class TestPickProvider(unittest.TestCase):
    def test_finds_first_match(self):
        ors    = OpenRouteServiceProvider(api_key="x")
        google = GoogleDistanceMatrixProvider(api_key="y")
        here   = HEREMatrixProvider(api_key="z")
        # need traffic + matrix — ors has no traffic, google does
        chosen = pick_provider([ors, google, here], supports_traffic=True)
        self.assertEqual(chosen.name, "google_distance_matrix")

    def test_finds_first_specific_capability(self):
        ors  = OpenRouteServiceProvider(api_key="x")
        here = HEREMatrixProvider(api_key="z")
        chosen = pick_provider([here, ors], supports_avoid_polygons=True)
        self.assertEqual(chosen.name, "openrouteservice")

    def test_returns_none_when_no_match(self):
        ors = OpenRouteServiceProvider(api_key="x")
        # ORS doesn't have traffic
        self.assertIsNone(pick_provider([ors], supports_traffic=True))


if __name__ == "__main__":
    unittest.main()
