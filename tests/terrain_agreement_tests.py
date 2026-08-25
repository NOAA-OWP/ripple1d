import json
import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from pyproj import CRS

from ripple1d.consts import MAP_DEM_UNCLIPPED_SRC_URL
from ripple1d.ops.ras_terrain import compute_terrain_agreement_metrics, create_ras_terrain

TEST_DIR = os.path.dirname(__file__)
MODEL = "PatuxentRiver"
SUBMODEL = "11906190"


@pytest.fixture(scope="class")
def setup_data(request):
    request.cls.model_path = os.path.join(TEST_DIR, "ras-data", MODEL, "submodels", SUBMODEL)
    request.cls.terrain_path = os.path.join(
        request.cls.model_path,
        "Terrain",
        SUBMODEL + "." + os.path.basename(MAP_DEM_UNCLIPPED_SRC_URL).replace(".vrt", ".tif"),
    )


@pytest.mark.usefixtures("setup_data")
class TestAgreementMetrics(unittest.TestCase):
    def test_endpoint(self):
        """End to end test to ensure integration of metrics function with endpoint caller."""
        create_ras_terrain(self.model_path)

    def test_db_output(self):
        result = compute_terrain_agreement_metrics(self.model_path, self.terrain_path, terrain_agreement_format="db")
        with sqlite3.connect(result) as con:
            cur = con.cursor()
            cur.execute("SELECT * FROM model_metrics")
            cur.execute("SELECT * FROM xs_metrics")
            cur.execute("SELECT * FROM xs_elevation_metrics")
        con.close()

    def test_json_output(self):
        result = compute_terrain_agreement_metrics(self.model_path, self.terrain_path, terrain_agreement_format="json")
        with open(result) as f:
            js = json.load(f)
        assert "model_metrics" in js
        assert "xs_metrics" in js
        assert "xs_elevation_metrics" in js["xs_metrics"][next(iter(js["xs_metrics"]))]

    def test_resolution(self):
        compute_terrain_agreement_metrics(self.model_path, self.terrain_path, terrain_agreement_resolution=0.5)
        compute_terrain_agreement_metrics(self.model_path, self.terrain_path, terrain_agreement_resolution=1e5)

    def test_resolution_units(self):
        compute_terrain_agreement_metrics(self.model_path, self.terrain_path, horizontal_units="Feet")
        compute_terrain_agreement_metrics(self.model_path, self.terrain_path, horizontal_units="Meters")

    def test_elevation_intervals(self):
        compute_terrain_agreement_metrics(self.model_path, self.terrain_path, terrain_agreement_el_init=0.1)
        compute_terrain_agreement_metrics(self.model_path, self.terrain_path, terrain_agreement_el_init=20)

        compute_terrain_agreement_metrics(self.model_path, self.terrain_path, terrain_agreement_el_repeats=1)
        compute_terrain_agreement_metrics(self.model_path, self.terrain_path, terrain_agreement_el_repeats=20)

        compute_terrain_agreement_metrics(self.model_path, self.terrain_path, terrain_agreement_el_ramp_rate=0.1)
        compute_terrain_agreement_metrics(self.model_path, self.terrain_path, terrain_agreement_el_ramp_rate=10)
