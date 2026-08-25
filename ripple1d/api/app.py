"""Flask API."""

import json
import logging
import time
import traceback
import typing
from http import HTTPStatus

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import BadRequest

from ripple1d.api import tasks
from ripple1d.api.utils import get_unexpected_and_missing_args
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

app = Flask(__name__)


@app.route("/processes/conflate_model/execution", methods=["POST"])
def process__conflate_model():
    """Enqueue a task to conflate a source model."""
    return enqueue_async_task(conflate_model)


@app.route("/processes/compute_conflation_metrics/execution", methods=["POST"])
def process__compute_conflation_metrics():
    """Enqueue a task to compute conflation metrics."""
    return enqueue_async_task(compute_conflation_metrics)


@app.route("/processes/gpkg_from_ras/execution", methods=["POST"])
def process__gpkg_from_ras():
    """Enqueue a task to create a new GeoPackage from a source model."""
    return enqueue_async_task(gpkg_from_ras)


@app.route("/processes/extract_submodel/execution", methods=["POST"])
def process__extract_submodel():
    """Enqueue a task to create a new GeoPackage for a NWM reach."""
    return enqueue_async_task(extract_submodel)


@app.route("/processes/create_ras_terrain/execution", methods=["POST"])
def process__create_ras_terrain():
    """Enqueue a task to create a new RAS terrain."""
    return enqueue_async_task(create_ras_terrain)


@app.route("/processes/create_model_run_normal_depth/execution", methods=["POST"])
def process__create_model_run_normal_depth():
    """Enqueue a task to calculate the initial normal depth."""
    return enqueue_async_task(create_model_run_normal_depth)


@app.route("/processes/run_incremental_normal_depth/execution", methods=["POST"])
def process__run_incremental_normal_depth():
    """Enqueue a task to calculate the incremental normal depth."""
    return enqueue_async_task(run_incremental_normal_depth)


@app.route("/processes/run_known_wse/execution", methods=["POST"])
def process__run_known_wse():
    """Enqueue a task to calculate the water surface elevation (WSE) based on known inputs."""
    return enqueue_async_task(run_known_wse)


@app.route("/processes/create_scenarios_db/execution", methods=["POST"])
def process__create_scenarios_db():
    """Enqueue a task to create a scenario db."""
    return enqueue_async_task(create_scenarios_db)


@app.route("/processes/create_fim_lib/execution", methods=["POST"])
def process__create_fim_lib():
    """Enqueue a task to create a FIM library."""
    return enqueue_async_task(create_fim_lib)


@app.route("/ping", methods=["GET"])
def ping():
    """Check the health of the service."""
    return jsonify({"status": "healthy"}), HTTPStatus.OK


@app.route("/processes/test/execution", methods=["POST"])
def test():
    """Test the execution and monitoring of an asynchronous task."""
    response, http_status = enqueue_async_task(tasks.noop)
    if http_status != HTTPStatus.CREATED:
        return jsonify(response.json, HTTPStatus.INTERNAL_SERVER_ERROR)

    timeout_seconds = 10

    start = time.time()
    while time.time() - start < timeout_seconds:
        time.sleep(0.2)
        status = tasks.fetch_ogc_status(response.json["jobID"])
        if status == "failed":
            return jsonify({"status": "not healthy"}), HTTPStatus.INTERNAL_SERVER_ERROR
        if status == "successful":
            return jsonify({"status": "healthy"}), HTTPStatus.OK
    return (
        jsonify({"status": f"huey is busy or not active, ping timed out after {timeout_seconds} seconds"}),
        HTTPStatus.GATEWAY_TIMEOUT,
    )


@app.route("/jobs", methods=["GET"])
def jobs():
    """Retrieve OGC status and result for all jobs."""
    format_option = request.args.get("f", default="json")
    task2metadata = tasks.task_status(only_task_id=None)
    jobs = [get_job_status(task_id, huey_metadata) for task_id, huey_metadata in task2metadata.items()]
    response = {"jobs": jobs}

    if format_option == "json":
        return jsonify(response), HTTPStatus.OK
    else:
        return render_template("jobs.html", response=response)


@app.route("/jobs/<task_id>", methods=["GET"])
def job_status(task_id):
    """Retrieve result for job."""
    include_traceback, problem = parse_request_param__bool(param_name="tb", default=False)
    if problem is not None:
        return problem
    task2metadata = tasks.task_summary(only_task_id=task_id)
    resp = get_job_status(task_id, task2metadata[task_id], return_result=True, include_traceback=include_traceback)
    try:
        return jsonify(resp), HTTPStatus.OK

    except Exception as e:
        return (
            jsonify(str(e)),
            HTTPStatus.NOT_FOUND,
        )


@app.route("/jobs/<task_id>/logs", methods=["GET"])
def job_logs(task_id):
    """Retrieve result for job."""
    try:
        result = tasks.fetch_logs(task_id)
        return jsonify(result), HTTPStatus.OK
    except Exception as e:
        return (
            jsonify(str(e)),
            HTTPStatus.NOT_FOUND,
        )


@app.route("/jobs/<task_id>/results", methods=["GET"])
def job_results(task_id):
    """Retrieve result for job."""
    try:
        result = tasks.fetch_results(task_id)
        return jsonify(result), HTTPStatus.OK
    except Exception as e:
        return (
            jsonify(str(e)),
            HTTPStatus.NOT_FOUND,
        )


@app.route("/jobs/<task_id>/metadata", methods=["GET"])
def job_metadata(task_id):
    """Retrieve metadata for job."""
    task2metadata = tasks.task_status(only_task_id=task_id)
    try:
        return jsonify(task2metadata), HTTPStatus.OK

    except Exception as e:
        return (
            jsonify(str(e)),
            HTTPStatus.NOT_FOUND,
        )


@app.route("/jobs/<task_id>", methods=["DELETE"])
def dismiss(task_id):
    """Dismiss a specific task by its ID.

    Available only for jobs in "accepted" status, dismissal of "running" jobs not implemented
    """
    try:
        ogc_status = tasks.fetch_ogc_status(task_id)
        if ogc_status == "notfound":
            return jsonify({"type": "process", "detail": f"job ID not found: {task_id}"}), HTTPStatus.NOT_FOUND

        elif ogc_status == "accepted":
            tasks.revoke_task(task_id)
            return jsonify({"type": "process", "detail": f"job ID dismissed: {task_id}"}), HTTPStatus.OK

        elif ogc_status == "running":
            tasks.revoke_task_by_pid(task_id)
            return jsonify({"type": "process", "detail": f"job ID dismissed: {task_id}"}), HTTPStatus.OK

        elif ogc_status == "dismissed":
            return jsonify({"type": "process", "detail": f"job ID dismissed: {task_id}"}), HTTPStatus.OK

        else:
            return (
                jsonify(
                    {
                        "type": "process",
                        "detail": f"failed to dismiss job ID {task_id} due to job status '{ogc_status}'",
                    }
                ),
                HTTPStatus.CONFLICT,
            )
    except Exception as e:
        return (
            jsonify(
                {
                    "type": "process",
                    "detail": f"failed to dismiss job ID {task_id} due to internal server error {str(e)}",
                }
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )


def parse_request_param__bool(param_name: str, default: bool) -> tuple[bool, tuple]:
    """Get the parameter, assert it is true or false, and return the appropriate Python boolean value as well as a status tuple.

    If there is a problem, the status tuple has two elements: a response message, and a HTTP status to be returned by the endpoint.
    If there is not a problem, the status tuple is actually None.
    """
    arg_tb = request.args.get(param_name)
    if not arg_tb:
        return (default, None)
    elif arg_tb.lower() == "false":
        return (False, None)
    elif arg_tb.lower() == "true":
        return (True, None)
    else:
        return (
            None,
            (
                jsonify(
                    {
                        "type": "process",
                        "detail": f"query param 'tb' should be 'true' or 'false', but got: {repr(arg_tb)}",
                    }
                ),
                HTTPStatus.BAD_REQUEST,
            ),
        )


def get_job_status(
    task_id: str, huey_metadata: dict, return_result: bool = False, include_traceback: bool = False
) -> dict:
    """Convert huey-style task status metadata into a OGC-style job summary dictionary."""
    out_dict = {
        "jobID": task_id,
        "updated": huey_metadata["status_time"],
        "status": huey_metadata["ogc_status"],
        "processID": huey_metadata["func_name"],
    }
    if return_result:
        if not include_traceback and huey_metadata["result"] is not None:
            del huey_metadata["result"]["tb"]
        out_dict["result"] = huey_metadata["result"]
    return out_dict


def enqueue_async_task(func: typing.Callable) -> tuple[Response, HTTPStatus]:
    """Start the execution of the provided func using kwargs from the request body. Assume body is a JSON dictionary."""
    try:
        kwargs = request.json  # can throw BadRequest when parsing body into json
        if not isinstance(kwargs, dict):
            raise BadRequest(f"expected body to be a JSON dictionary, but got: {type(kwargs)}")
    except BadRequest as e:
        return (
            jsonify({"type": "process", "detail": f"could not parse body to json dict. error: {e}"}),
            HTTPStatus.BAD_REQUEST,
        )

    unexpected, missing = get_unexpected_and_missing_args(func, set(kwargs))
    if unexpected or missing:
        return (
            jsonify({"type": "process", "detail": f"unexpected args: {unexpected}, missing args: {missing}"}),
            HTTPStatus.BAD_REQUEST,
        )

    try:
        result = tasks.create_and_enqueue_task(func, kwargs)
    except:
        msg = f"unable to submit task: {traceback.format_exc()}"
        app.logger.error(msg)
        return jsonify({"type": "process", "detail": msg}), HTTPStatus.INTERNAL_SERVER_ERROR
    else:
        return (
            jsonify({"type": "process", "jobID": result.id, "status": "accepted"}),
            HTTPStatus.CREATED,
        )
