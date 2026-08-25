import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from pyproj import CRS

from ripple1d.ras import RasFlowText, RasGeomText, RasManager, RasPlanText, RasProject

TEST_DIR = os.path.dirname(__file__)
TEST_ITEM_FILE = "ras-data/baxter.json"
TEST_ITEM_PATH = os.path.join(TEST_DIR, TEST_ITEM_FILE)

RAS_PROJECT = os.path.join(TEST_DIR, "ras-data/Baxter/Baxter.prj")
RAS_PLAN = os.path.join(TEST_DIR, "ras-data/Baxter/Baxter.p01")
RAS_GEOM = os.path.join(TEST_DIR, "ras-data/Baxter/Baxter.g02")
RAS_FLOW = os.path.join(TEST_DIR, "ras-data/Baxter/Baxter.f01")
PROJECTION_FILE = os.path.join(TEST_DIR, "ras-data/Baxter/CA_SPCS_III_NAVD88.prj")
NEW_GPKG = os.path.join(TEST_DIR, "ras-data/Baxter/Baxter_test.gpkg")


@pytest.fixture(scope="class")
def setup_data(request):
    with open(PROJECTION_FILE, "r") as f:
        crs = f.read()
    request.cls.PROJECTION = crs
    request.cls.ras_project = RasProject(RAS_PROJECT)
    request.cls.ras_plan = RasPlanText(RAS_PLAN)
    request.cls.ras_geom = RasGeomText(RAS_GEOM, crs=CRS(crs))
    request.cls.ras_flow = RasFlowText(RAS_FLOW)


# RasProjectText
@pytest.mark.usefixtures("setup_data")
class TestProject(unittest.TestCase):
    def test_load_data(self):
        self.assertEqual(len(self.ras_project.contents), 36)
        self.assertEqual(self.ras_project.title, "Baxter River GIS Example")
        for plan in self.ras_project.plans:
            extension = Path(plan).suffix
            self.assertIn(extension, [".p01", ".p02"])

        for geom in self.ras_project.geoms:
            extension = Path(geom).suffix
            self.assertIn(extension, [".g02"])

        for steady_flow in self.ras_project.steady_flows:
            extension = Path(steady_flow).suffix
            self.assertIn(extension, [".f01"])

        for unsteady_flow in self.ras_project.unsteady_flows:
            extension = Path(unsteady_flow).suffix
            self.assertIn(extension, [".u01", ".u02", ".u03"])

    def test_new_project(self):
        pass


# RasPlanText
@pytest.mark.usefixtures("setup_data")
class TestPlan(unittest.TestCase):
    def test_load_data(self):
        self.assertEqual(len(self.ras_plan.contents), 171)
        self.assertEqual(self.ras_plan.title, "Steady Flows")
        self.assertEqual(self.ras_plan.version, "5.00")
        self.assertEqual(self.ras_plan.plan_geom_extension, ".g02")
        self.assertEqual(self.ras_plan.plan_steady_extension, ".f01")

    def test_new_plan(self):
        pass


# RasGeomText
@pytest.mark.usefixtures("setup_data")
class TestGeom(unittest.TestCase):
    def test_load_data(self):
        self.assertEqual(self.ras_geom.title, "Imported GIS Data +Bridges")
        self.assertEqual(self.ras_geom.version, ["5.00"])

    def test_parser(self):
        self.assertEqual(len(self.ras_geom.reaches), 3)
        self.assertIn("Baxter River    ,Upper Reach     ", self.ras_geom.reaches.keys())

    # def test_to_gpkg(self):
    #     self.ras_geom.to_gpkg(NEW_GPKG)


# RasFlowText
@pytest.mark.usefixtures("setup_data")
class TestFlow(unittest.TestCase):
    def test_load_data(self):
        self.assertEqual(self.ras_flow.title, "Steady Flows")
        self.assertEqual(self.ras_flow.version, "6.30")
        self.assertEqual(self.ras_flow.n_profiles, 3)

    def test_new_flow(self):
        pass


# # RasManagerText
# class TestRasManager(unittest.TestCase):
#     @patch("platform.system", return_value="Linux")
#     def test_run_sim_on_non_windows(self, _):
#         ras_manager = RasManager(RAS_PROJECT)
#         with self.assertRaises(SystemError):
#             ras_manager.run_sim()
