import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from api_tests import start_server, submit_job, wait_for_job


def main(test_model: str = None, reach_id: str = None, clean_up: bool = True):

    current_dir = os.path.dirname(__file__)

    if not test_model:
        test_models = ["Baxter", "MissFldwy", "PatuxentRiver", "winooski"]
    else:
        test_models = [test_model]

    if not reach_id:
        reach_ids = [
            "2826228",
            "2823932",
            "2823960",
            "2826230",
            "2823934",
            "2826224",
            "2823920",
            "2821866",
            "2820012",
            "2820006",
            "2823972",
            "2820002",
            "2930557",
            "11906190",
            "4576958",
            "4578832",
            "4576956",
        ]
    else:
        reach_ids = [reach_id]
    start_server()
    time.sleep(10)

    p, results_files = [], []
    fails = {}
    passes = 0
    for test_model in test_models:
        pre_process = subprocess.Popen(
            [
                sys.executable.replace("python.exe", "pytest.exe"),
                "tests/api_tests.py::TestPreprocessAPI",
                "--model",
                test_model,
                "--reach_id",
                "1",
                "--min_elevation",
                "1",
            ]
        )
        pre_process.wait()
        conflation_file = os.path.join(current_dir, "ras-data", test_model, f"{test_model}.conflation.json")
        if not os.path.exists(conflation_file):
            raise FileNotFoundError(f"Conflation file not found: {conflation_file}")

        with open(conflation_file, "r") as f:
            data = json.loads(f.read())

        for reach_id in reach_ids:
            reach_id = str(reach_id)

            if reach_id not in data["reaches"].keys():
                continue
            sub_model_dir = os.path.join(current_dir, "ras-data", test_model, "submodels", reach_id)
            if os.path.exists(sub_model_dir):
                shutil.rmtree(sub_model_dir, ignore_errors=True)
            results_files.append(os.path.join(current_dir, f"{test_model}-{reach_id}.json"))
            p.append(
                subprocess.Popen(
                    [
                        sys.executable.replace("python.exe", "pytest.exe"),
                        "--json-report",
                        "--json-report-omit",
                        "keywords",
                        "collectors",
                        "--json-report-file",
                        results_files[-1],
                        "--json-report-indent",
                        "4",
                        r"tests/api_tests.py::TestApi",
                        "--model",
                        test_model,
                        "--reach_id",
                        reach_id,
                        "--min_elevation",
                        str(data["reaches"][reach_id]["ds_xs"]["min_elevation"]),
                    ]
                )
            )

    for results_file in results_files:
        test_model, reach_id = Path(results_file).name.rstrip(".json").split("-")
        with open(results_file, "r") as f:
            results = json.loads(f.read())
        if "passed" in results["summary"]:
            passes += results["summary"]["passed"]
        if "failed" not in results["summary"]:
            os.remove(results_file)
            sub_model_dir = os.path.join(current_dir, "ras-data", test_model, "submodels", reach_id)
            if os.path.exists(sub_model_dir):
                shutil.rmtree(sub_model_dir, ignore_errors=True)

        else:
            for test in results["tests"]:
                if test["outcome"] != "passed":
                    fails.update({f"{test_model}-{reach_id}": test})

    fails["pass count"] = passes
    with open(os.path.join(current_dir, "summary_api_tests.json"), "w") as f:
        json.dump(fails, f, indent=4)


if __name__ == "__main__":
    """By default (providing no args) this script will run all test reach ids for the Baxter test model and
    "2930557" and  "11906190" for the MissFldwy and PatuxentRiver test models, respectively.

    # NOTE: For this test ensure pytest-json-report is installed:  pip install pytest-json-report
    Example 1 usage:
    python tests/run_api_tests.py

    # Note: When running and rerunning tests, ensure you delete the /submodels directory for each model
    # included in the tests,and turn off the servers (close the terminals)

    Alternatively, you can specify the test model and reach id to run as args.
    Example 2 usage:
    python tests/run_api_tests.py Baxter 2826228
    """
    main(*sys.argv[1:])
