import os
import unittest

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, MultiLineString, Point, Polygon

from ripple1d.conflate.rasfim import (
    RasFimConflater,
    _clamp_nd_slope,
    cacl_avg_nearest_points,
    count_intersecting_lines,
    endpoints_from_multiline,
    get_ds_boundary_slope,
    nearest_line_to_point,
)
from ripple1d.consts import DEFAULT_ND_SLOPE, MAX_ND_SLOPE, MIN_ND_SLOPE

TEST_DIR = os.path.dirname(__file__)
TEST_ITEM_FILE = "ras-data/Baxter.json"
TEST_ITEM_PATH = os.path.join(TEST_DIR, TEST_ITEM_FILE)

# Expected counts
NWM_REACHES = 16
LOCAL_NWM_REACHES = 16
RAS_CENTERLINES = 3
RAS_XS = 173
GAGES = 1

# Other expected data
RIVER_REACHES = [
    "Baxter River, Upper Reach",
    "Tule Creek, Tributary",
    "Baxter River, Lower Reach",
]

NWM_REACHES_DATA = "flows.parquet"
NWM_REACH_IDS = [2826228]
RAS_DIR = "Baxter"
RAS_GEOMETRY_GPKG = "Baxter.gpkg"


@pytest.fixture(scope="class")
def setup_data(request):
    nwm_pq_path = os.path.join(TEST_DIR, "nwm-data", NWM_REACHES_DATA)
    source_model_directory = os.path.join(TEST_DIR, "ras-data", RAS_DIR)
    conflater = RasFimConflater(nwm_pq_path, source_model_directory, RAS_DIR)
    request.cls.conflater = conflater


@pytest.mark.usefixtures("setup_data")
class TestRasFimConflater(unittest.TestCase):
    def test_load_data(self):
        self.conflater.load_data()

    def test_ras_centerlines_exist(self):
        centerlines = self.conflater.ras_centerlines
        self.assertEqual(centerlines.shape[0], RAS_CENTERLINES)

    # def test_ras_river_reach_names_exist(self):
    #     reach_names = self.conflater.ras_river_reach_names
    #     self.assertEqual(reach_names, RIVER_REACHES)
    #     self.assertEqual(len(reach_names), RAS_CENTERLINES)

    def test_ras_xs_exist(self):
        ras_xs = self.conflater.ras_xs
        self.assertEqual(ras_xs.shape[0], RAS_XS)

    def test_ras_xs_bbox_is_polygon(self):
        bbox = self.conflater.ras_xs_bbox
        self.assertIsInstance(bbox, Polygon)

    def test_nwm_reaches_exist(self):
        nwm_reaches = self.conflater.nwm_reaches
        self.assertEqual(nwm_reaches.shape[0], NWM_REACHES)

    def test_local_nwm_reaches_exist(self):
        local_reaches = self.conflater.local_nwm_reaches
        self.assertEqual(local_reaches().shape[0], LOCAL_NWM_REACHES)

    def test_local_gages_exist(self):
        gages = self.conflater.local_gages
        self.assertEqual(len(gages), GAGES)

    # geospatial operations
    def test_endpoints_from_multiline(self):
        mline = MultiLineString([LineString([(0, 0), (1, 1)]), LineString([(1, 1), (2, 2)])])
        start, end = endpoints_from_multiline(mline)
        self.assertEqual(start, Point(0, 0))
        self.assertEqual(end, Point(2, 2))

    def test_nearest_line_to_point(self):
        lines = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])], "ID": [1]})
        point = Point(0.5, 0.5)
        line_id = nearest_line_to_point(lines, point)
        self.assertEqual(line_id, 1)

    def test_cacl_avg_nearest_points(self):
        reference_gdf = gpd.GeoDataFrame({"geometry": [Point(0, 0), Point(1, 1)]})
        compare_points_gdf = gpd.GeoDataFrame({"geometry": [Point(0, 0), Point(1, 1), Point(2, 2)]})
        avg_distance = cacl_avg_nearest_points(reference_gdf, compare_points_gdf)
        self.assertAlmostEqual(avg_distance, 0.0)

    def test_count_intersecting_lines(self):
        ras_xs = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])]}, crs="EPSG:4326")
        nwm_reaches = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])]}, crs="EPSG:4326")
        count = count_intersecting_lines(ras_xs, nwm_reaches)
        self.assertEqual(count.shape[0], 1)

    def _make_rfc(self, xs_geometry, reach_ids, to_ids, slopes, reach_geometries):
        nwm_reaches = gpd.GeoDataFrame({
            "ID": reach_ids, "to_id": to_ids, "slope": slopes,
            "geometry": reach_geometries,
        })
        walker = type("MockWalker", (), {"tree_dict": dict(zip(reach_ids, to_ids))})()
        return type("TestConflater", (), {
            "ras_xs": gpd.GeoDataFrame({
                "river": ["A"], "reach": ["A"], "river_station": [1.0],
                "geometry": [xs_geometry],
            }),
            "nwm_reaches": nwm_reaches,
            "nwm_walker": walker,
        })()

    def test_ds_boundary_slope_uses_current_reach_when_ds_xs_intersects_current_reach(self):
        rfc = self._make_rfc(
            xs_geometry=LineString([(1, -1), (1, 1)]),
            reach_ids=[1, 2], to_ids=[2, -9999], slopes=[0.001, 0.005],
            reach_geometries=[LineString([(0, 0), (2, 0)]), LineString([(2, 0), (4, 0)])],
        )
        slope, source_id = get_ds_boundary_slope(rfc, 1, {"ds_xs": {"river": "A", "reach": "A", "xs_id": 1}})
        self.assertEqual(slope, 0.001)
        self.assertEqual(source_id, "1")

    def test_ds_boundary_slope_prefers_downstream_reach_when_ds_xs_intersects_both_reaches(self):
        rfc = self._make_rfc(
            xs_geometry=LineString([(2, -1), (2, 1)]),
            reach_ids=[1, 2], to_ids=[2, -9999], slopes=[0.001, 0.005],
            reach_geometries=[LineString([(0, 0), (2, 0)]), LineString([(2, 0), (4, 0)])],
        )
        slope, source_id = get_ds_boundary_slope(rfc, 1, {"ds_xs": {"river": "A", "reach": "A", "xs_id": 1}})
        self.assertEqual(slope, 0.005)
        self.assertEqual(source_id, "2")

    def test_ds_boundary_slope_falls_back_to_current_reach_when_no_candidate_on_downstream_path(self):
        # xs intersects reaches 2 and 3, but neither is downstream of current reach 1
        rfc = self._make_rfc(
            xs_geometry=LineString([(2, -1), (2, 1)]),
            reach_ids=[1, 2, 3], to_ids=[-9999, -9999, -9999], slopes=[0.002, 0.005, 0.007],
            reach_geometries=[
                LineString([(10, 0), (12, 0)]),  # reach 1: far from xs (current reach)
                LineString([(0, 0), (2, 0)]),    # reach 2: intersects xs
                LineString([(2, 0), (4, 0)]),    # reach 3: intersects xs
            ],
        )
        slope, source_id = get_ds_boundary_slope(rfc, 1, {"ds_xs": {"river": "A", "reach": "A", "xs_id": 1}})
        self.assertEqual(slope, 0.002)
        self.assertEqual(source_id, "1")

    def test_ds_boundary_slope_falls_back_to_current_reach_when_no_candidates(self):
        # xs is far from all reaches, so candidates is empty; current reach has valid slope
        rfc = self._make_rfc(
            xs_geometry=LineString([(100, -1), (100, 1)]),
            reach_ids=[1], to_ids=[-9999], slopes=[0.003],
            reach_geometries=[LineString([(0, 0), (2, 0)])],
        )
        slope, source_id = get_ds_boundary_slope(rfc, 1, {"ds_xs": {"river": "A", "reach": "A", "xs_id": 1}})
        self.assertEqual(slope, 0.003)
        self.assertEqual(source_id, "1")

    def test_ds_boundary_slope_falls_back_to_default_when_no_valid_slope_anywhere(self):
        rfc = self._make_rfc(
            xs_geometry=LineString([(1, -1), (1, 1)]),
            reach_ids=[1], to_ids=[-9999], slopes=[np.nan],
            reach_geometries=[LineString([(0, 0), (2, 0)])],
        )
        slope, source_id = get_ds_boundary_slope(rfc, 1, {"ds_xs": {"river": "A", "reach": "A", "xs_id": 1}})
        self.assertEqual(slope, DEFAULT_ND_SLOPE)
        self.assertIsNone(source_id)

    # _clamp_nd_slope unit tests
    def test_clamp_nd_slope_below_floor_returns_floor(self):
        self.assertEqual(_clamp_nd_slope(5e-7), MIN_ND_SLOPE)

    def test_clamp_nd_slope_above_ceiling_returns_ceiling(self):
        self.assertEqual(_clamp_nd_slope(0.3), MAX_ND_SLOPE)

    def test_clamp_nd_slope_at_exact_floor_unchanged(self):
        self.assertEqual(_clamp_nd_slope(MIN_ND_SLOPE), MIN_ND_SLOPE)

    def test_clamp_nd_slope_at_exact_ceiling_unchanged(self):
        self.assertEqual(_clamp_nd_slope(MAX_ND_SLOPE), MAX_ND_SLOPE)

    def test_clamp_nd_slope_within_bounds_unchanged(self):
        for value in (0.005, DEFAULT_ND_SLOPE, 9.999999747378752e-06):
            with self.subTest(value=value):
                self.assertEqual(_clamp_nd_slope(value), value)


# TODO: Update to remove refernce to windows User directory
# @pytest.mark.usefixtures("setup_data")
# class TestConflationExample(unittest.TestCase):
#     def setUp(self):
#         self.conflater.load_data()

#     def test_main_function(self):
#         metadata = conflate(self.conflater)
#         for reach in NWM_REACHE_IDS:
#             self.assertIn(reach, metadata.keys())

#         test_data_results = os.path.join(TEST_DIR, "ras-data", RAS_DIR, "Baxter.conflation.json")
#         with open(test_data_results, "r") as f:
#             expected_metadata = f.read()
#             self.assertEqual(json.dumps(metadata, indent=4), expected_metadata)
