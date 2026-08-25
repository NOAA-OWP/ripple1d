import json
import os
import shutil
import subprocess
import time
import unittest

import pandas as pd
import pytest
import requests

from ripple1d.ras import RasFlowText


def start_server():
    return subprocess.Popen(["ripple1d", "start", "--thread_count", "10"])


def submit_job(process: str, payload: dict):
    headers = {"Content-Type": "application/json"}
    url = f"http://localhost/processes/{process}/execution"
    response = requests.post(url, data=json.dumps(payload), headers=headers)
    return json.loads(response.text)


def wait_for_job(job_id: str):
    url = f"http://localhost/jobs/{job_id}?tb=true"
    while True:
        response = requests.get(url)
        job_status = response.json().get("status")
        if job_status in ["successful", "failed"]:

            return job_status
        time.sleep(7)  # Wait for 10 seconds before checking again


def check_process(func):

    def wrapper(self, *args, **kwargs):
        process, payload, files = func(self)
        response = submit_job(process, payload)
        status = wait_for_job(response["jobID"])
        time.sleep(1)

        self.assertEqual(status, "successful")
        for file in files:
            self.assertTrue(os.path.exists(file))

    return wrapper


@pytest.mark.usefixtures("setup_data")
class TestPreprocessAPI(unittest.TestCase):
    @check_process
    def test_a_gpkg_from_ras(self):
        payload = {
            "source_model_directory": self.SOURCE_RAS_MODEL_DIRECTORY,
            "crs": self.crs,
            "metadata": {
                "stac_api": "https://stac2.dewberryanalytics.com",
                "stac_collection_id": "ebfe-12090301_LowerColoradoCummins",
                "stac_item_id": "137a9667-e5cf-4cea-b6ec-2e882a42fdc8",
            },
        }
        if os.path.exists(self.SOURCE_GPKG_FILE):
            os.remove(self.SOURCE_GPKG_FILE)
        process = "gpkg_from_ras"
        files = [self.SOURCE_GPKG_FILE]
        return process, payload, files

    @check_process
    def test_b_conflation(self):
        payload = {
            "source_model_directory": self.SOURCE_RAS_MODEL_DIRECTORY,
            "model_name": self.MODEL_NAME,
            "source_network": {"file_name": self.SOURCE_NETWORK, "version": "2.1", "type": "nwm_hydrofabric"},
        }
        process = "conflate_model"
        if os.path.exists(self.conflation_file):
            os.remove(self.conflation_file)
        files = [self.conflation_file]
        return process, payload, files

    @check_process
    def test_c_compute_conflation_metrics(self):
        payload = {
            "source_model_directory": self.SOURCE_RAS_MODEL_DIRECTORY,
            "model_name": self.MODEL_NAME,
            "source_network": {"file_name": self.SOURCE_NETWORK, "version": "2.1", "type": "nwm_hydrofabric"},
        }
        process = "compute_conflation_metrics"
        files = [self.conflation_file]
        return process, payload, files


@pytest.mark.usefixtures("setup_data")
class TestApi(unittest.TestCase):

    @check_process
    def test_b_extract_submodel(self):
        payload = {
            "source_model_directory": self.SOURCE_RAS_MODEL_DIRECTORY,
            "submodel_directory": self.SUBMODELS_DIRECTORY,
            "nwm_id": self.REACH_ID,
            "model_name": self.MODEL_NAME,
        }
        process = "extract_submodel"
        files = [self.GPKG_FILE]
        return process, payload, files

    @check_process
    def test_c_create_ras_terrain(self):
        payload = {"submodel_directory": self.SUBMODELS_DIRECTORY}
        process = "create_ras_terrain"
        files = [self.TERRAIN_HDF, self.TERRAIN_VRT]
        return process, payload, files

    @check_process
    def test_d_create_model_run_normal_depth(self):
        payload = {
            "submodel_directory": self.SUBMODELS_DIRECTORY,
            "plan_suffix": "ind",
            "num_of_discharges_for_initial_normal_depth_runs": 2,
            "show_ras": False,
        }
        process = "create_model_run_normal_depth"
        files = [self.RAS_PROJECT_FILE, self.GEOM_FILE, self.PLAN1_FILE, self.FLOW1_FILE, self.RESULT1_FILE]
        return process, payload, files

    @check_process
    def test_e_run_incremental_normal_depth(self):
        payload = {
            "submodel_directory": self.SUBMODELS_DIRECTORY,
            "plan_suffix": "nd",
            "depth_increment": 3,
            "ras_version": "631",
            "show_ras": False,
        }
        process = "run_incremental_normal_depth"
        files = [self.PLAN2_FILE, self.FLOW2_FILE, self.RESULT2_FILE]
        return process, payload, files

    @check_process
    def test_f_run_known_wse_initial(self):
        payload = {
            "submodel_directory": self.SUBMODELS_DIRECTORY,
            "plan_suffix": "ikwse",
            "min_elevation_curve": [[0, self.min_elevation + 40]],
            "max_elevation": self.min_elevation + 41,
            "depth_increment": 1.0,
            "ras_version": "631",
            "show_ras": False,
            "write_depth_grids": False,
        }
        process = "run_known_wse"
        files = [self.PLAN3_FILE, self.FLOW3_FILE, self.RESULT3_FILE]
        return process, payload, files

    @check_process
    def test_g_create_scenarios_db_initial(self):

        payload = {
            "submodel_directory": self.SUBMODELS_DIRECTORY,
            "plans": ["ikwse"],
        }
        process = "create_scenarios_db"
        files = [self.FIM_LIB_DB]
        return process, payload, files

    @check_process
    def test_h_run_known_wse(self):
        payload = {
            "submodel_directory": self.SUBMODELS_DIRECTORY,
            "plan_suffix": "kwse",
            "min_elevation_curve": [[0, self.min_elevation + 40]],
            "max_elevation": self.min_elevation + 41,
            "depth_increment": 1.0,
            "ras_version": "631",
            "show_ras": False,
            "write_depth_grids": True,
        }
        process = "run_known_wse"
        files = [self.PLAN4_FILE, self.FLOW4_FILE, self.RESULT4_FILE]
        return process, payload, files

    @check_process
    def test_i_create_scenarios_db(self):

        payload = {
            "submodel_directory": self.SUBMODELS_DIRECTORY,
            "plans": ["nd", "kwse"],
        }
        process = "create_scenarios_db"
        files = [self.FIM_LIB_DB]
        return process, payload, files

    @check_process
    def test_j_create_fim_lib(self):

        payload = {
            "submodel_directory": self.SUBMODELS_DIRECTORY,
            "plans": ["nd", "kwse"],
            "library_directory": self.FIM_LIB_DIRECTORY,
            "cleanup": False,
            "cog": True,
        }
        process = "create_fim_lib"
        files = [self.DEPTH_GRIDS_ND, self.DEPTH_GRIDS_KWSE]
        return process, payload, files

    def test_k_check_flows_are_equal(self):
        rf2 = pd.DataFrame(RasFlowText(self.FLOW2_FILE).flow_change_locations)
        rf3 = pd.DataFrame(RasFlowText(self.FLOW3_FILE).flow_change_locations)
        self.assertTrue(len(set(rf3["flows"].iloc[0]) - set(rf2["flows"].iloc[0])) == 0)

    # @check_process
    # def test_h_nwm_reach_model_stac(self):
    #     payload = {"ras_project_directory": self.SUBMODELS_DIRECTORY}
    #     process = "nwm_reach_model_stac"
    #     files = [self.MODEL_STAC_ITEM]
    #     return process, payload, files

    # @check_process
    # def test_i_fim_lib_stac(self):
    #     payload = {"ras_project_directory": self.SUBMODELS_DIRECTORY, "nwm_reach_id": self.REACH_ID}
    #     process = "fim_lib_stac"
    #     files = [self.FIM_LIB_STAC_ITEM]
    #     return process, payload, files
