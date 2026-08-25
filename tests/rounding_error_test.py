import os
import time

import psutil

from ripple1d.ops.ras_run import run_known_wse

TIMEOUT = 60
WAIT_TIME = 0.1


def wait_for_process(pid: int):
    run_time = 0
    while psutil.pid_exists(pid):
        if run_time < TIMEOUT:
            time.sleep(WAIT_TIME)
            run_time += WAIT_TIME
        else:
            psutil.Process(pid).terminate()
            raise RuntimeError("RAS run timed out.")


def test_kwse_run():
    """Check that problematic model runs with no errors."""
    model_dir = os.path.join(os.path.dirname(__file__), "test-data", "14320639")
    payload = {
        "submodel_directory": model_dir,
        "plan_suffix": "kwse",
        "show_ras": True,
        "write_depth_grids": False,
        "min_elevation_curve": [[0, 182.2]],
        "max_elevation": 194.1,
        "depth_increment": 1,
    }
    result = run_known_wse(**payload)
    wait_for_process(int(result["pid"]))
    [os.remove(os.path.join(model_dir, f)) for f in os.listdir(model_dir) if f.endswith("03") or f.endswith("03.hdf")]


if __name__ == "__main__":
    test_kwse_run()
