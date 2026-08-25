import os

import numpy as np
import pytest

TEST_DIR = os.path.dirname(__file__)


def pytest_addoption(parser):
    parser.addoption("--model", action="store", help="Test Source HEC-RAS Model", default="Baxter")
    parser.addoption("--reach_id", action="store", help="NWM Reach ID", default="2823932")
    parser.addoption(
        "--min_elevation", action="store", help="Min elevation of the most downstream cross section", default="0"
    )


@pytest.fixture
def setup_data(request):

    RAS_MODEL = request.config.getoption("--model")
    REACH_ID = request.config.getoption("--reach_id")
    MIN_ELEVATION = float(request.config.getoption("--min_elevation"))
    CRS = {"Baxter": 2227, "PatuxentRiver": 6488, "MissFldwy": 32165, "winooski": 5646}

    SOURCE_NETWORK = os.path.join(TEST_DIR, f"nwm-data\\flows.parquet")
    SOURCE_RAS_MODEL_DIRECTORY = os.path.join(TEST_DIR, f"ras-data\\{RAS_MODEL}")
    SUBMODELS_BASE_DIRECTORY = os.path.join(SOURCE_RAS_MODEL_DIRECTORY, "submodels")
    SUBMODELS_DIRECTORY = os.path.join(SUBMODELS_BASE_DIRECTORY, REACH_ID)
    SUBMODEL_NAME = REACH_ID
    FIM_LIB_DIRECTORY = os.path.join(SUBMODELS_DIRECTORY, f"fims")
    request.cls.FIM_LIB_DIRECTORY = FIM_LIB_DIRECTORY
    request.cls.REACH_ID = RAS_MODEL
    request.cls.REACH_ID = REACH_ID
    request.cls.SOURCE_NETWORK = SOURCE_NETWORK
    request.cls.SOURCE_RAS_MODEL_DIRECTORY = SOURCE_RAS_MODEL_DIRECTORY
    request.cls.MODEL_NAME = RAS_MODEL
    request.cls.SUBMODELS_BASE_DIRECTORY = SUBMODELS_BASE_DIRECTORY
    request.cls.SUBMODELS_DIRECTORY = SUBMODELS_DIRECTORY
    request.cls.GPKG_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.gpkg")
    request.cls.SOURCE_GPKG_FILE = os.path.join(SOURCE_RAS_MODEL_DIRECTORY, f"{RAS_MODEL}.gpkg")
    request.cls.TERRAIN_HDF = os.path.join(SUBMODELS_DIRECTORY, f"Terrain\\{REACH_ID}.hdf")
    request.cls.TERRAIN_VRT = os.path.join(SUBMODELS_DIRECTORY, f"Terrain\\{REACH_ID}.vrt")
    request.cls.RAS_PROJECT_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.prj")
    request.cls.GEOM_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.g01")
    request.cls.PLAN1_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.p01")
    request.cls.FLOW1_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.f01")
    request.cls.RESULT1_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.r01")
    request.cls.PLAN2_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.p02")
    request.cls.FLOW2_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.f02")
    request.cls.RESULT2_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.r02")
    request.cls.PLAN3_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.p03")
    request.cls.FLOW3_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.f03")
    request.cls.RESULT3_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.r03")
    request.cls.PLAN4_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.p04")
    request.cls.FLOW4_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.f04")
    request.cls.RESULT4_FILE = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.r04")

    request.cls.FIM_LIB_DB = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.db")
    request.cls.DEPTH_GRIDS_ND = os.path.join(FIM_LIB_DIRECTORY, f"{REACH_ID}\\z_nd")
    ceil_k = int(np.floor((MIN_ELEVATION + 41) + 0.5))
    kwse_dir = str(round(float(ceil_k), 1)).replace(".", "_")
    request.cls.DEPTH_GRIDS_KWSE = os.path.join(FIM_LIB_DIRECTORY, f"{REACH_ID}\\z_{kwse_dir}")
    request.cls.MODEL_STAC_ITEM = os.path.join(SUBMODELS_DIRECTORY, f"{REACH_ID}.model.stac.json")
    request.cls.FIM_LIB_STAC_ITEM = os.path.join(SUBMODELS_DIRECTORY, f"fims\\{REACH_ID}.fim_lib.stac.json")
    request.cls.min_elevation = MIN_ELEVATION
    request.cls.conflation_file = os.path.join(SOURCE_RAS_MODEL_DIRECTORY, f"{RAS_MODEL}.conflation.json")
    request.cls.crs = CRS[RAS_MODEL]
