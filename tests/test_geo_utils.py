"""Pure-math helpers — no I/O, no mocks needed."""
import math
import unittest

from . import _helpers   # adds project root to sys.path
from geo_utils import (avoid_polygons_geojson, buffer_point, buffer_point_geojson,
                       decode_polyline, haversine_m, point_in_polygon,
                       point_in_ring)


class TestHaversine(unittest.TestCase):
    def test_zero_distance(self):
        self.assertEqual(int(haversine_m(34.0, 32.0, 34.0, 32.0)), 0)

    def test_known_distance_telaviv_haifa(self):
        # ~93 km straight-line per multiple references
        d = haversine_m(34.7741, 32.0809, 34.9896, 32.7940)
        self.assertAlmostEqual(d / 1000, 81, delta=10)

    def test_symmetry(self):
        d1 = haversine_m(34.7, 32.1, 35.2, 31.8)
        d2 = haversine_m(35.2, 31.8, 34.7, 32.1)
        self.assertAlmostEqual(d1, d2, delta=0.1)


class TestBufferPoint(unittest.TestCase):
    def test_ring_is_closed(self):
        ring = buffer_point(34.7741, 32.0809, 500, vertices=8)
        # closed ring: first == last
        self.assertEqual(ring[0], ring[-1])
        self.assertEqual(len(ring), 9)   # 8 vertices + closing copy

    def test_radius_accuracy(self):
        ring = buffer_point(34.7741, 32.0809, 1000, vertices=16)
        # sample 4 vertices, all should be within ±2% of 1km from the centre
        for vertex in [ring[0], ring[4], ring[8], ring[12]]:
            d = haversine_m(34.7741, 32.0809, vertex[0], vertex[1])
            self.assertGreater(d, 980)
            self.assertLess(d, 1020)

    def test_min_vertices_validation(self):
        with self.assertRaises(ValueError):
            buffer_point(34, 32, 100, vertices=3)

    def test_geojson_wrapping(self):
        gj = buffer_point_geojson(34, 32, 100)
        self.assertEqual(gj["type"], "Polygon")
        self.assertEqual(len(gj["coordinates"]), 1)


class TestPointInPolygon(unittest.TestCase):
    def setUp(self):
        # 1 km square around Dizengoff
        self.poly = buffer_point_geojson(34.7741, 32.0809, 1000, vertices=64)

    def test_centre_is_inside(self):
        self.assertTrue(point_in_polygon(34.7741, 32.0809, self.poly))

    def test_far_point_outside(self):
        self.assertFalse(point_in_polygon(34.5, 32.0809, self.poly))

    def test_handles_multipolygon(self):
        mp = avoid_polygons_geojson([
            buffer_point(34.7741, 32.0809, 500)[:-1],
            buffer_point(35.2, 31.78, 500)[:-1],
        ])
        self.assertTrue(point_in_polygon(34.7741, 32.0809, mp))
        self.assertTrue(point_in_polygon(35.2, 31.78, mp))
        self.assertFalse(point_in_polygon(34.0, 31.0, mp))

    def test_unknown_geometry_returns_false(self):
        self.assertFalse(point_in_polygon(0, 0, {"type": "LineString"}))

    def test_ring_membership(self):
        ring = buffer_point(34.7741, 32.0809, 500, vertices=32)
        self.assertTrue(point_in_ring(34.7741, 32.0809, ring))
        self.assertFalse(point_in_ring(0, 0, ring))


class TestDecodePolyline(unittest.TestCase):
    def test_google_canonical_example(self):
        # Standard Google docs example.
        pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        self.assertEqual(len(pts), 3)
        # Each pair is [lng, lat].
        self.assertAlmostEqual(pts[0][1], 38.5,  delta=0.01)
        self.assertAlmostEqual(pts[0][0], -120.2, delta=0.01)
        self.assertAlmostEqual(pts[2][1], 43.252, delta=0.01)

    def test_empty(self):
        self.assertEqual(decode_polyline(""), [])

    def test_precision_6(self):
        # Same input, precision 6 → coordinates 10× smaller magnitude
        pts5 = decode_polyline("_p~iF~ps|U", precision=5)
        pts6 = decode_polyline("_p~iF~ps|U", precision=6)
        self.assertAlmostEqual(pts5[0][0] / pts6[0][0], 10.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
