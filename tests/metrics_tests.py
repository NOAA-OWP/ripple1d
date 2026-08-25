import os
import unittest

import geopandas as gpd
import pytest
from shapely.ops import linemerge

from ripple1d.ops.metrics import ConflationMetrics, combine_reaches, compute_conflation_metrics
from ripple1d.ops.subset_gpkg import RippleGeopackageSubsetter
from ripple1d.utils.ripple_utils import fix_reversed_xs

# Expected counts
NETWORK_REACHES = 1
LOCAL_NETWORK_REACHES = 18
SRC_RAS_CENTERLINES = 3
SRC_RAS_XS = 173
SRC_RAS_STRUCTURES = 7
RIPPLE_RAS_CENTERLINES = 1
RIPPLE_RAS_XS = 51
RIPPLE_RAS_STRUCTURES = 2
NETWORK_REACH_COORDS_LEN = 74

# Other expected data
US_RIVER = "Baxter River"
US_REACH = "Upper Reach"
US_XS_ID = 78658.0
DS_RIVER = "Baxter River"
DS_REACH = "Lower Reach"
DS_XS_ID = 47694.0
INTERSECTION_DELTA_XY_MEAN = 23
THALWEG_INTERSECTION_DELTA_XY_MEAN = 27
RAS_REACH_LENGTH = 30814
NETWORK_REACH_LENGTH = 30535
NETWORK_TO_RAS_RATIO = 0.99
START = 0.01
END = 1

TEST_DIR = os.path.dirname(__file__)

NETWORK_REACHES_DATA = "flows.parquet"
RAS_DIR = "Baxter"
RAS_GEOMETRY_GPKG = "Baxter.gpkg"
CONFLATION_JSON = "Baxter.conflation.json"
NETWORK_REACH_ID = 2823960
NETWORK_TO_ID = 2823972
NETWORK_CRS = 5070


@pytest.fixture(scope="class")
def setup_data(request):
    network_pq_path = os.path.join(TEST_DIR, "nwm-data", NETWORK_REACHES_DATA)
    src_gpkg_path = os.path.join(TEST_DIR, "ras-data", RAS_DIR, RAS_GEOMETRY_GPKG)
    conflation_json = os.path.join(TEST_DIR, "ras-data", RAS_DIR, CONFLATION_JSON)
    rgs = RippleGeopackageSubsetter(src_gpkg_path, conflation_json, "", str(NETWORK_REACH_ID))

    layers = {}
    for layer, gdf in rgs.subset_gdfs.items():
        layers[layer] = gdf.to_crs(NETWORK_CRS)
    network_reaches = gpd.read_parquet(network_pq_path, bbox=layers["XS"].total_bounds)
    network_reach = linemerge(network_reaches.loc[network_reaches["ID"] == int(NETWORK_REACH_ID)].geometry.iloc[0])
    network_reach_plus_ds_reach = combine_reaches(network_reaches, NETWORK_REACH_ID)

    cm = ConflationMetrics(
        fix_reversed_xs(layers["XS"], layers["River"]),
        layers["River"],
        rgs.ripple_xs_concave_hull,
        network_reach,
        network_reach_plus_ds_reach,
        NETWORK_REACH_ID,
    )
    request.cls.rgs = rgs
    request.cls.cm = cm
    request.cls.network_reaches = network_reaches
    request.cls.network_reach = network_reach
    request.cls.network_to_id = NETWORK_TO_ID
    request.cls.network_id = NETWORK_REACH_ID
    request.cls.network_crs = NETWORK_CRS


@pytest.mark.usefixtures("setup_data")
class TestRippleGeopackageSubsetter(unittest.TestCase):
    def test_load_data(self):
        self.rgs.ripple1d_parameters
        self.rgs.source_xs
        self.rgs.source_river
        self.rgs.source_structure

    def test_src_ras_centerlines_exist(self):
        self.assertEqual(self.rgs.source_river.shape[0], SRC_RAS_CENTERLINES)

    def test_src_ras_xs_exist(self):
        self.assertEqual(self.rgs.source_xs.shape[0], SRC_RAS_XS)

    def test_src_ras_structures_exist(self):
        self.assertEqual(self.rgs.source_structure.shape[0], SRC_RAS_STRUCTURES)

    def test_ripple_xs_exist(self):
        self.assertEqual(self.rgs.ripple_xs.shape[0], RIPPLE_RAS_XS)

    def test_ripple_river_exist(self):
        self.assertEqual(self.rgs.ripple_river.shape[0], RIPPLE_RAS_CENTERLINES)

    def test_ripple_structures_exist(self):
        self.assertEqual(self.rgs.ripple_structure.shape[0], RIPPLE_RAS_STRUCTURES)

    def test_us_river(self):
        self.assertEqual(self.rgs.us_river, US_RIVER)

    def test_us_reach(self):
        self.assertEqual(self.rgs.us_reach, US_REACH)

    def test_us_rs(self):
        self.assertEqual(self.rgs.us_rs, US_XS_ID)

    def test_ds_river(self):
        self.assertEqual(self.rgs.ds_river, DS_RIVER)

    def test_ds_reach(self):
        self.assertEqual(self.rgs.ds_reach, DS_REACH)

    def test_ds_rs(self):
        self.assertEqual(self.rgs.ds_rs, DS_XS_ID)


@pytest.mark.usefixtures("setup_data")
class TestConflationMetrics(unittest.TestCase):
    def test_network_reaches_exist(self):
        network_reaches = self.network_reaches.loc[self.network_reaches["ID"] == int(self.network_id), :]
        self.assertEqual(network_reaches.shape[0], NETWORK_REACHES)
        self.assertEqual(len(self.network_reach.coords), NETWORK_REACH_COORDS_LEN)

    def test_network_to_id(self):
        to_id = self.network_reaches.loc[self.network_reaches["ID"] == int(self.network_id), "to_id"].iloc[0]
        self.assertEqual(to_id, self.network_to_id)

    def test_xs(self):
        self.assertEqual(self.cm.xs_gdf.shape[0], RIPPLE_RAS_XS)

    def test_thalweg_metrics(self):
        self.assertEqual(
            self.cm.thalweg_metrics(self.rgs.ripple_xs.to_crs(self.network_crs))["centerline_offset"]["mean"],
            INTERSECTION_DELTA_XY_MEAN,
        )
        self.assertEqual(
            self.cm.thalweg_metrics(self.rgs.ripple_xs.to_crs(self.network_crs))["thalweg_offset"]["mean"],
            THALWEG_INTERSECTION_DELTA_XY_MEAN,
        )

    def test_reach_length_metrics(self):
        self.assertEqual(self.cm.length_metrics(self.rgs.ripple_xs.to_crs(self.network_crs))["ras"], RAS_REACH_LENGTH)
        self.assertEqual(
            self.cm.length_metrics(self.rgs.ripple_xs.to_crs(self.network_crs))["network"], NETWORK_REACH_LENGTH
        )
        self.assertEqual(
            self.cm.length_metrics(self.rgs.ripple_xs.to_crs(self.network_crs))["network_to_ras_ratio"],
            NETWORK_TO_RAS_RATIO,
        )

    def test_coverage_metrics(self):
        self.assertEqual(self.cm.compute_coverage_metrics(self.rgs.ripple_xs.to_crs(self.network_crs))["start"], START)
        self.assertEqual(self.cm.compute_coverage_metrics(self.rgs.ripple_xs.to_crs(self.network_crs))["end"], END)
