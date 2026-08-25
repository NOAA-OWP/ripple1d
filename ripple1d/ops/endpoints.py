"""Endpoint utilites for executing ripple processes using subprocess calls."""

import json
import logging
import subprocess
import sys
from typing import Callable

from ripple1d.consts import SUPPRESS_LOGS
from ripple1d.hecstac.ras_to_gpkg import gpkg_from_ras
from ripple1d.ops.fim_lib import create_fim_lib, create_scenarios_db
from ripple1d.ops.metrics import compute_conflation_metrics
from ripple1d.ops.ras_conflate import conflate_model
from ripple1d.ops.ras_run import (
    create_model_run_normal_depth,
    run_incremental_normal_depth,
    run_known_wse,
)
from ripple1d.ops.ras_terrain import create_ras_terrain
from ripple1d.ops.subset_gpkg import extract_submodel
from ripple1d.ripple1d_logger import RippleLogFormatter  # , initialize_process_logger

func_lookup = {
    "extract_submodel": extract_submodel,
    "conflate_model": conflate_model,
    "compute_conflation_metrics": compute_conflation_metrics,
    "gpkg_from_ras": gpkg_from_ras,
    "create_ras_terrain": create_ras_terrain,
    "create_model_run_normal_depth": create_model_run_normal_depth,
    "run_incremental_normal_depth": run_incremental_normal_depth,
    "run_known_wse": run_known_wse,
    "create_fim_lib": create_fim_lib,
    "create_scenarios_db": create_scenarios_db,
}


def execute_endpoint(func: str, args: dict):
    """Execute the specified function."""
    print("at api lookup")
    return func_lookup[func](**json.loads(args))


def initialize_process_logger(level, verbose: bool = False):
    """Configure logging for ripple1d."""
    datefmt = "%Y-%m-%dT%H:%M:%SZ"

    for module in SUPPRESS_LOGS:
        logging.getLogger(module).setLevel(logging.WARNING)

    handler = logging.StreamHandler(sys.stdout)
    logging.basicConfig(
        level=level,
        handlers=[handler],
        format="""{"time": "%(asctime)s" , "level": "%(levelname)s", "msg": "%(message)s"}""",
    )


if __name__ == "__main__":
    initialize_process_logger(logging.INFO)
    try:
        r = execute_endpoint(sys.argv[1], sys.argv[2])
        print((json.dumps({"results": r})))
    except Exception as e:
        raise SystemError(f"Exception occurred: {e}")
        sys.exit(1)
