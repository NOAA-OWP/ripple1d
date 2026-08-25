"""Run HEC-RAS models."""

import json
import logging
from dataclasses import asdict

import numpy as np
import pandas as pd

from ripple1d.consts import DEFAULT_EPSG, DEFAULT_ND_SLOPE, MIN_FLOW
from ripple1d.data_model import FlowChangeLocation, NwmReachModel
from ripple1d.errors import UnitsError
from ripple1d.ras import RasManager

# Allowed KWSE stage increments (ft). Restricted so that we can be consistent with one-decimal
# precision, a 0.25 would break one decimal precision rule.
ALLOWED_DEPTH_INCREMENTS = (0.5, 1, 2, 5, 10)


def create_model_run_normal_depth(
    submodel_directory: str,
    plan_suffix: str,
    num_of_discharges_for_initial_normal_depth_runs: int = 10,
    ras_version: str = "631",
    show_ras: bool = False,
):
    """Write and compute initial normal depth runs to develop initial scenarios.

    Parameters
    ----------
    submodel_directory : str
        The path to the directory containing a sub model geopackage
    plan_suffix : str
        characters to append to the end of the plan name, by default "_ind"
    num_of_discharges_for_initial_normal_depth_runs : int, optional
        number of discharges to run, evenly spaced between low and high flow
        limits, by default 10
    ras_version : str, optional
        which version of HEC-RAS to use, by default "631"
    show_ras : bool, optional
        whether to run HEC-RAS headless or not, by default False
    task_id : str, optional
        Task ID to use for logging, by default ""

    Returns
    -------
    str
        string representation of flow file data

    Raises
    ------
    FileNotFoundError
        raised when .conflation.json file not found in submodel_directory
    FileNotFoundError
        raised when geopackage file not found in submodel_directory

    Notes
    -----
    create_model_run_normal_depth is intended to create an initial
    stage-discharge rating curve for the HEC-RAS submodel. Analysis flows are
    evenly spaced between the min and max discharge for the reach that were
    established by running conflate_model.  The downstream boundary condition
    for these runs are set to normal depth using the NWM reach slope for the
    submodel's final downstream cross section (falls back to DEFAULT_ND_SLOPE if
    unavailable). The conflation step bounds the value to [MIN_ND_SLOPE, MAX_ND_SLOPE].
    """
    logging.info(f"create_model_run_normal_depth starting")
    nwm_rm = NwmReachModel(submodel_directory)

    if not nwm_rm.file_exists(nwm_rm.conflation_file):
        raise FileNotFoundError(f"cannot find conflation file {nwm_rm.conflation_file}, please ensure file exists")

    if not nwm_rm.file_exists(nwm_rm.ras_gpkg_file):
        raise FileNotFoundError(f"cannot find ras_gpkg_file file {nwm_rm.ras_gpkg_file}, please ensure file exists")

    if nwm_rm.units != "English":
        raise UnitsError(f"Can only process 'English' units at this time. '{nwm_rm.units}' was provided")

    if "u" in nwm_rm.flow_extension or "q" in nwm_rm.flow_extension:
        raise ValueError(
            f"Only steady state source models are supported at this time. The provided flow file for the source model is {nwm_rm.flow_file} which is not supported"
        )

    if nwm_rm.ripple1d_parameters["eclipsed"] == True:
        logging.warning(f"skipping {nwm_rm.model_name}; no cross sections conflated.")
    else:
        logging.info(f"Working on initial normal depth run for nwm_id: {nwm_rm.model_name}")

        # create new ras manager class
        rm = RasManager.from_gpkg(nwm_rm.ras_project_file, nwm_rm.model_name, nwm_rm.ras_gpkg_file, ras_version)

        # increment flows based on min and max flows specified in conflation parameters
        initial_flows = np.linspace(
            max([nwm_rm.ripple1d_parameters["low_flow"], MIN_FLOW]),
            nwm_rm.ripple1d_parameters["high_flow"],
            num_of_discharges_for_initial_normal_depth_runs,
        ).astype(int)

        # # write and compute initial normal depth runs to develop initial scenarios
        fcl = FlowChangeLocation(
            nwm_rm.model_name,
            nwm_rm.model_name,
            rm.geoms[nwm_rm.model_name].rivers[nwm_rm.model_name][nwm_rm.model_name].us_xs.river_station_str,
            initial_flows.tolist(),
        )

        profile_name_map = {str(key): str(val) for key, val in zip(range(len(initial_flows)), initial_flows)}

        pid = rm.normal_depth_run(
            f"{nwm_rm.model_name}_{plan_suffix}",
            nwm_rm.model_name,
            [fcl],
            [i for i in profile_name_map.keys()],
            normal_depth=nwm_rm.ripple1d_parameters.get("ds_slope", DEFAULT_ND_SLOPE),
            write_depth_grids=False,
            show_ras=show_ras,
            run_ras=True,
            flow_file_description=json.dumps(profile_name_map),
        )

    logging.info("create_model_run_normal_depth complete")
    return {f"{nwm_rm.model_name}_{plan_suffix}": asdict(fcl), "pid": pid}


def run_incremental_normal_depth(
    submodel_directory: str,
    plan_suffix: str,
    ras_version: str = "631",
    depth_increment=0.5,
    write_depth_grids: str = True,
    show_ras: bool = False,
):
    """Write and compute incremental normal depth runs to develop scenarios and depth grids.

    Parameters
    ----------
    submodel_directory : str
        The path to the directory containing a sub model geopackage
    plan_suffix : str
        characters to append to the end of the plan name, by default "_nd"
    ras_version : str, optional
        which version of HEC-RAS to use, by default "631"
    depth_increment : float, optional
        stage increment to use for developing the stage-discharge rating curve,
        by default 0.5
    write_depth_grids : str, optional
        whether to generate depth rasters after each model run, by default True
    show_ras : bool, optional
        whether to run HEC-RAS headless or not, by default False
    task_id : str, optional
        Task ID to use for logging, by default ""

    Returns
    -------
    str
        string representation of flow file data

    Raises
    ------
    FileNotFoundError
        raised when .conflation.json file not found in submodel_directory

    Notes
    -----
    run_incremental_normal_depth is intended to resample a stage-discharge
    rating curve generated with create_model_run_normal_depth to a consistent
    stage increment. Min and max stages for the curve are taken from the sub
    model plan with suffix "_ind".  A set of evenly spaced stages are selected
    between the min and max, and discharge values are estimated with linear
    interpolation. The final set of estimated discharges are then run through
    the model with a normal depth downstream boundary condition using the NWM
    reach slope for the submodel's final downstream cross section (falls back to
    DEFAULT_ND_SLOPE if unavailable). The conflation step bounds the value to
    [MIN_ND_SLOPE, MAX_ND_SLOPE].
    """
    logging.info("run_incremental_normal_depth starting")
    nwm_rm = NwmReachModel(submodel_directory)

    if not nwm_rm.file_exists(nwm_rm.conflation_file):
        raise FileNotFoundError(f"cannot find conflation file {nwm_rm.conflation_file}, please ensure file exists")

    logging.info(f"Working on normal depth run for nwm_id: {nwm_rm.model_name}")
    if nwm_rm.ripple1d_parameters["eclipsed"] == True:
        logging.warning(f"skipping {nwm_rm.model_name}; no cross sections conflated.")

    rm = RasManager(
        nwm_rm.ras_project_file,
        version=ras_version,
        terrain_path=nwm_rm.ras_terrain_hdf,
        crs=nwm_rm.crs,
    )

    # determine flow increments
    flows, _, _ = determine_flow_increments(
        rm,
        [f"{nwm_rm.model_name}_ind"],
        nwm_rm.model_name,
        nwm_rm.model_name,
        nwm_rm.model_name,
        depth_increment=depth_increment,
    )

    fcl = FlowChangeLocation(
        nwm_rm.model_name,
        nwm_rm.model_name,
        rm.geoms[nwm_rm.model_name].rivers[nwm_rm.model_name][nwm_rm.model_name].us_xs.river_station_str,
        flows.tolist(),
    )

    profile_name_map = {str(key): str(val) for key, val in zip(range(len(flows)), flows)}

    # write and compute flow/plans for normal_depth run
    pid = rm.normal_depth_run(
        f"{nwm_rm.model_name}_{plan_suffix}",
        nwm_rm.model_name,
        [fcl],
        [i for i in profile_name_map.keys()],
        normal_depth=nwm_rm.ripple1d_parameters.get("ds_slope", DEFAULT_ND_SLOPE),
        write_depth_grids=write_depth_grids,
        show_ras=show_ras,
        run_ras=True,
        flow_file_description=json.dumps(profile_name_map),
    )
    logging.info("run_incremental_normal_depth complete")
    return {f"{nwm_rm.model_name}_{plan_suffix}": asdict(fcl), "pid": pid}


def run_known_wse(
    submodel_directory: str,
    plan_suffix: str,
    min_elevation_curve: list[list[float]],
    max_elevation: float,
    depth_increment: float,
    ras_version: str = "631",
    write_depth_grids: str = True,
    show_ras: bool = False,
):
    """Write and compute known water surface elevation runs to develop scenarios and depth grids.

    Parameters
    ----------
    submodel_directory : str
        The path to the directory containing a sub model geopackage
    plan_suffix : str
        characters to append to the end of the plan name, by default "_kwse"
    min_elevation_curve : list[list[float]]
        Tailwater min elevation curve as ``[discharge, wse]`` pairs. Each flow's floor
        elevation is looked up from this curve (see Notes).
    max_elevation : float
        Ceiling elevation: the shared upper bound of the downstream boundary
    depth_increment : float
        depth to increment stages between the floor and the ceiling, must be one of
        ALLOWED_DEPTH_INCREMENTS = (0.5, 1, 2, 5, 10)
    ras_version : str, optional
        which version of HEC-RAS to use, by default "631"
    write_depth_grids : str, optional
        whether to generate depth rasters after each model run, by default True
    show_ras : bool, optional
        whether to run HEC-RAS headless or not, by default False
    task_id : str, optional
        Task ID to use for logging, by default ""

    Returns
    -------
    dict
        mapping of the kwse plan name to the known water surface elevations used,
        and the HEC-RAS process id under "pid"

    Raises
    ------
    FileNotFoundError
        raised when .conflation.json file not found in submodel_directory
    ValueError
        raised when min_elevation_curve or max_elevation is missing

    Notes
    -----
    run_known_wse creates a catalog of scenarios conditioned on
    downstream water surface elevation. Discharges are selected from the HEC-RAS
    plan with suffix "_nd" generated with run_incremental_normal_depth. Each
    discharge is executed over its own range, at depth_increment steps.

    The floor for a discharge is looked up from min_elevation_curve data point:
    the wse of the greatest tabulated discharge that is lower or equal to the flow.
    Flows below the lowest tabulated discharge clamp to the lowest floor; flows at
    or above the highest hold the highest floor.


    """
    logging.info("run_known_wse starting")

    if not min_elevation_curve:
        raise ValueError("run_known_wse requires a non-empty min_elevation_curve")
    if max_elevation is None:
        raise ValueError("run_known_wse requires max_elevation")
    if depth_increment not in ALLOWED_DEPTH_INCREMENTS:
        raise ValueError(f"depth_increment must be one of {ALLOWED_DEPTH_INCREMENTS}, got {depth_increment}")

    nwm_rm = NwmReachModel(submodel_directory)

    if not nwm_rm.file_exists(nwm_rm.conflation_file):
        raise FileNotFoundError(f"cannot find conflation file {nwm_rm.conflation_file}, please ensure file exists")

    logging.info(f"Working on known water surface elevation run for nwm_id: {nwm_rm.model_name}")

    # write and compute flow/plans for known water surface elevation runs
    rm = RasManager(nwm_rm.ras_project_file, version=ras_version, terrain_path=nwm_rm.ras_terrain_hdf, crs=nwm_rm.crs)

    # get the flows from the second normal depth run (_nd), each flow is executed over
    # its own [floor, ceiling] elevation range
    rm.plan = rm.plans[f"{nwm_rm.model_name}_nd"]
    ds_xs = rm.geoms[nwm_rm.model_name].rivers[nwm_rm.model_name][nwm_rm.model_name].ds_xs
    ds_flows, _, _ = get_flow_depth_arrays(
        rm,
        nwm_rm.model_name,
        nwm_rm.model_name,
        ds_xs.river_station_str,
        ds_xs.thalweg,
    )

    depths, flows, wses = create_flow_wse_scenarios_matrix(
        ds_flows,
        min_elevation_curve,
        max_elevation,
        depth_increment,
        ds_xs.thalweg,
    )

    if not flows:
        logging.warning(
            f"No known water surface elevations were identified for {nwm_rm.model_name}; "
            "i.e., every flow's floor elevation was above max_elevation."
        )
        pid = None
    else:
        pid = rm.kwses_run(
            f"{nwm_rm.model_name}_{plan_suffix}",
            nwm_rm.model_name,
            depths,
            wses,
            flows,
            nwm_rm.model_name,
            nwm_rm.model_name,
            rm.geoms[nwm_rm.model_name].rivers[nwm_rm.model_name][nwm_rm.model_name].us_xs.river_station_str,
            write_depth_grids=write_depth_grids,
            show_ras=show_ras,
            run_ras=True,
        )
    logging.info("run_known_wse complete")
    return {f"{nwm_rm.model_name}_{plan_suffix}": {"kwse": sorted(set(wses))}, "pid": pid}


def get_flow_depth_arrays(
    rm: RasManager, river: str, reach: str, river_station: str, thalweg: float
) -> tuple[pd.Series]:
    """Create new flow, depth,wse arrays from plan results."""
    # read in flow/wse
    profile_name_map = json.loads(rm.plan.flow.description)
    wses, flows = rm.plan.read_plan_results(profile_name_map)

    # get the river_reach_rs for the cross section representing the upstream end of this reach
    river_reach_rs = f"{river} {reach} {str(river_station)}"

    wse = wses.loc[river_reach_rs, :]
    flow = flows.loc[river_reach_rs, :]
    df = pd.DataFrame({"wse": wse.round(1), "flow": flow.astype(int)}).drop_duplicates()

    # convert wse to depth
    depth = df["wse"] - thalweg

    return (df["flow"], depth, df["wse"])


def determine_flow_increments(
    rm: RasManager,
    plan_names: str,
    river: str,
    reach: str,
    nwm_id: str,
    depth_increment: float = 0.5,
) -> tuple[np.array]:
    """Determine flow increments corresponding to 0.5 ft depth increments using the normal-depth-run results."""
    flows, depths = [], []
    for plan_name in plan_names:
        rm.plan = rm.plans[plan_name]

        river_station = rm.geoms[nwm_id].rivers[nwm_id][nwm_id].us_xs.river_station_str
        thalweg = rm.geoms[nwm_id].rivers[nwm_id][nwm_id].us_xs.thalweg

        # get new flow/depth for current branch
        flow, depth, _ = get_flow_depth_arrays(rm, river, reach, river_station, thalweg)
        flows.append(np.array(flow))
        depths.append(np.array(depth))
    # get new flow/depth incremented every x ft
    new_depths, new_flows = create_flow_depth_array(np.concatenate(flows), np.concatenate(depths), depth_increment)

    new_wse = new_depths + thalweg  # [i + thalweg for i in new_depths]

    return new_flows.astype(int), new_depths, new_wse


def stepwise_floor_lookup(flow: float, curve: list[list[float]]) -> float:
    """Return the floor WSE for a flow using previous-value (last-known) lookup.

    Parameters
    ----------
    flow : float
        Discharge to look up.
    curve : list[list[float]]
        Tailwater lower-bound curve as ``[discharge, wse]`` pairs. The floor is the
        wse of the greatest tabulated discharge that is <= flow. Flows below the
        lowest tabulated discharge clamp to the lowest floor; flows at or above the
        highest discharge hold the highest floor.

    Returns
    -------
    float
        The floor WSE for the flow.
    """
    sorted_curve = sorted(curve, key=lambda pair: pair[0])
    floor = sorted_curve[0][1]  # clamp below-range flows to the lowest floor
    for discharge, wse in sorted_curve:
        if discharge <= flow:
            floor = wse
        else:
            break
    return floor


def create_flow_wse_scenarios_matrix(
    reach_flows: pd.Series,
    min_elevation_curve: list[list[float]],
    max_elevation: float,
    depth_increment: float,
    thalweg: float,
) -> tuple:
    """Sweep each flow over its own [floor, ceiling] known-WSE envelope.

    Parameters
    ----------
    reach_flows : pd.Series
        Discharges from the most downstream cross section from the normal depth runs
        for current reach.
    min_elevation_curve : list[list[float]]
        Tailwater lower-bound curve as ``[discharge, wse]`` pairs.
    max_elevation : float
        Ceiling elevation shared by every flow.
    depth_increment : float
        Elevation step between the floor and the ceiling.
    thalweg : float
        Most downstream cross-section thalweg, used to convert WSE to depth.

    Returns
    -------
    tuple
        Parallel ``(depths, flows, wses)`` lists, one entry per (flow, wse) profile.

    Notes
    -----
    The grid is anchored by 0 (so ``Δz=1`` gives whole feet, ``Δz=0.5`` half feet, ``Δz=2``
    even feet), and every discharge therefore shares one grid regardless of where its tailwater
    falls. Both the shared ceiling (``max_elevation``) and each discharge's floor are rounded to
    the nearest grid line.
    """

    def nearest_k(elevation):
        """Index of the nearest absolute grid line k*inc, rounding halves up."""
        return int(np.floor(elevation / depth_increment + 0.5))

    depths, flows, wses = [], [], []
    ceil_k = nearest_k(max_elevation)  # shared max ceiling, same for every discharge
    for flow in reach_flows:
        floor = stepwise_floor_lookup(flow, min_elevation_curve)
        for k in range(nearest_k(floor), ceil_k + 1):
            # keep wse a float: an integer depth_increment makes k*inc an int, which would
            # name the library "z_736" instead of "z_736_0" (see str(wse) in ras profile names)
            wse = round(float(k * depth_increment), 1)
            depths.append(round(wse - thalweg, 1))
            flows.append(int(flow))
            wses.append(wse)
    return (depths, flows, wses)


def get_kwse_from_ds_model(ds_nwm_id: str, ds_nwm_ras_project_file: str, plan_names: str) -> tuple[float]:
    """Get the kwse values from the downstream model."""
    rm = RasManager(ds_nwm_ras_project_file, crs=DEFAULT_EPSG)
    wses = []
    for plan_name in plan_names:
        if plan_name not in rm.plans.keys():
            logging.warning(f"{plan_name} is not an existing plan in the specified HEC-RAS model")
            return np.array([])

        rm.plan = rm.plans[plan_name]

        river_reach_rs = rm.plan.geom.rivers[ds_nwm_id][ds_nwm_id].us_xs.river_reach_rs
        thalweg = rm.plan.geom.rivers[ds_nwm_id][ds_nwm_id].us_xs.thalweg

        wse, _ = rm.plan.read_plan_results()

        wses.append(wse.loc[river_reach_rs, :])

        df = pd.concat(wses)
    return df.min(), df.max()


def establish_order_of_nwm_ids(conflation_parameters: dict) -> list[str]:
    """Establish the order of NWM IDs based on the cross section IDs."""
    order = []
    for idx, data in conflation_parameters.items():
        if conflation_parameters[id]["us_xs"]["xs_id"] == "-9999":
            logging.warning(f"skipping {idx}; no cross sections conflated.")
        else:
            order.append((float(data["us_xs"]["xs_id"]), idx))
    order.sort()
    return [i[1] for i in order]


def create_flow_depth_array(flow: list[float], depth: list[float], increment: float = 0.5) -> tuple[np.array]:
    """Interpolate flow values to a new depth array with a specified increment."""
    min_depth = np.min(depth)
    max_depth = np.max(depth)
    start_depth = np.floor(min_depth / increment) * increment  # round down to nearest increment
    new_depth = np.arange(start_depth, max_depth + increment, increment)
    new_depth = np.clip(new_depth, depth.min(), depth.max())  # "new_flow" will be limited to "flow" range by np.interp.
    # This line makes "new_depth" max and min line up with those values.
    new_flow = np.interp(new_depth, np.sort(depth), np.sort(flow))

    return new_depth, new_flow
