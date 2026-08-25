"""Create FIM library."""

import glob
import json
import logging
import os
import shutil

import rasterio
from pyproj import CRS

# from osgeo import gdal
from rasterio.enums import Resampling

from ripple1d.data_model import NwmReachModel
from ripple1d.errors import DepthGridNotFoundError
from ripple1d.ras import RasManager
from ripple1d.utils.dg_utils import (
    reproject_raster,
)
from ripple1d.utils.sqlite_utils import (
    create_db_and_table,
    scenarios_to_sqlite,
    zero_depth_to_sqlite,
)


def post_process_depth_grids(
    rm: RasManager,
    plan_name: str,
    dest_directory: str,
    accept_missing_grid: bool = False,
    cog=False,
    dest_crs: CRS = 5070,
    resolution: float = 3,
    resolution_units: str = "Meters",
) -> tuple[list[str]]:
    """Clip depth grids based on their associated NWM branch and respective cross sections."""
    if resolution and not resolution_units:
        raise ValueError(
            f"The 'resolution' arg has been provided but 'resolution_units' arg has not been provided. Please provide both"
        )

    if resolution_units:
        if resolution_units not in ["Feet", "Meters"]:
            raise ValueError(f"Invalid resolution_units: {resolution_units}. expected 'Feet' or 'Meters'")

    profile_name_map = json.loads(rm.plans[plan_name].flow.description)
    for profile_name in rm.plans[plan_name].flow.profile_names:
        # construct the default path to the depth grid for this plan/profile
        src_dir = os.path.join(rm.ras_project._ras_dir, str(plan_name))
        terrain_dir = os.path.join(rm.ras_project._ras_dir, "Terrain")
        terrain_part = os.path.basename(glob.glob(terrain_dir + "\\*.tif")[0]).split(".")[-2]
        src_path = os.path.join(
            src_dir,
            f"Depth ({profile_name}).{rm.ras_project.title}.{terrain_part}.tif",
        )

        # if the depth grid path does not exists print a warning then continue to the next profile
        if not os.path.exists(src_path):
            if accept_missing_grid:
                logging.warning(f"depth raster does not exists: {src_path}")
                continue
            else:
                raise DepthGridNotFoundError(f"depth raster does not exists: {src_path}")

        new_profile_name = profile_name_map[profile_name]
        if "kwse" in plan_name:
            flow, depth = new_profile_name.split("-", 1)
        elif "nd" in plan_name:
            flow = f"f_{new_profile_name}"
            depth = "z_nd"

        flow_sub_directory = os.path.join(dest_directory, depth)
        os.makedirs(flow_sub_directory, exist_ok=True)
        dest_path = os.path.join(flow_sub_directory, f"{flow}.tif")
        logging.debug(dest_path)
        reproject_raster(src_path, dest_path, CRS(dest_crs), resolution, resolution_units, tiled=True)
        logging.debug(f"Building overviews for: {dest_path}")

        if cog:
            with rasterio.open(dest_path, "r+") as dst:
                dst.build_overviews([4, 8, 16], Resampling.nearest)
                dst.update_tags(ns="rio_overview", resampling="nearest")


def create_scenarios_db(
    submodel_directory: str, plans: list, ras_version: str = "631", table_name: str = "scenarios"
):
    """Create a new scenario database for a NWM id.

    Export computed flow/stage scenarios from HEC-RAS to
    rasters and a sqlite database.

    Parameters
    ----------
    submodel_directory : str
        The path to the directory containing a sub model geopackage
    plans : list
        suffixes of plans to create fim library for.
    ras_version : str, optional
        which version of HEC-RAS to use, by default "631"
    table_name : str, optional
        name for the table holding the computed scenarios in the output
        database, by default "scenarios"

    Returns
    -------
    dict
        dictionary with paths to output scenario database
    """
    logging.info(f"create_scenarios_db starting")

    nwm_rm = NwmReachModel(submodel_directory, submodel_directory)

    rm = RasManager(
        nwm_rm.ras_project_file,
        version=ras_version,
        terrain_path=nwm_rm.ras_terrain_hdf,
        crs=nwm_rm.crs,
    )

    # create dabase and table
    if not os.path.exists(nwm_rm.fim_results_database):
        create_db_and_table(nwm_rm.fim_results_database, table_name)

    for plan in plans:
        if f"{nwm_rm.model_name}_{plan}" not in rm.plans:
            logging.error(f"Plan {nwm_rm.model_name}_{plan} not found in the model, skipping...")
            continue
        else:
            missing_grids = find_missing_grids(rm, f"{nwm_rm.model_name}_{plan}")

        if f"kwse" in plan:
            scenarios_to_sqlite(
                rm,
                f"{nwm_rm.model_name}_{plan}",
                plan,
                nwm_rm.model_name,
                missing_grids,
                nwm_rm.fim_results_database,
                table_name,
            )
        if f"nd" in plan:
            zero_depth_to_sqlite(
                rm,
                f"{nwm_rm.model_name}_{plan}",
                plan,
                nwm_rm.model_name,
                missing_grids,
                nwm_rm.fim_results_database,
                table_name,
            )

    logging.info(f"create_scenarios_db complete")
    return {"scenario_database": nwm_rm.fim_results_database}


def find_missing_grids(
    rm: RasManager,
    plan_name: str,
):
    """Find missing depth grids."""
    missing_grids = []
    for profile_name in rm.plans[plan_name].flow.profile_names:
        # construct the default path to the depth grid for this plan/profile
        src_dir = os.path.join(rm.ras_project._ras_dir, str(plan_name))
        terrain_dir = os.path.join(rm.ras_project._ras_dir, "Terrain")
        terrain_part = os.path.basename(glob.glob(terrain_dir + "\\*.tif")[0]).split(".")[-2]
        src_path = os.path.join(
            src_dir,
            f"Depth ({profile_name}).{rm.ras_project.title}.{terrain_part}.tif",
        )

        # if the depth grid path does not exists print a warning then continue to the next profile
        if not os.path.exists(src_path):
            missing_grids.append(profile_name)

    # rename to flow-formateed profile name
    profile_name_map = json.loads(rm.plans[plan_name].flow.description)
    missing_grids = [profile_name_map[i] for i in missing_grids]
    return missing_grids


def create_fim_lib(
    submodel_directory: str,
    plans: list,
    library_directory: str,
    cleanup: bool,
    ras_version: str = "631",
    cog: bool = False,
    resolution: float = 3,
    resolution_units: str = "Meters",
    dest_crs: str = 5070,
):
    """Create a new FIM library for a NWM id.

    Export depth rasters and computed flow/stage scenarios from HEC-RAS to
    rasters and a sqlite database.

    Parameters
    ----------
    submodel_directory : str
        The path to the directory containing a sub model geopackage
    plans : list
        suffixes of plans to create fim library for.
    library_directory : str
        No function
    cleanup : bool
        whether to delete the source depth grids once they've been processed
    ras_version : str, optional
        which version of HEC-RAS to use, by default "631"
    cog : bool, optional
        whether to generate COGs for output rasters (overviews at levels
        [4, 8, 16]), by default False
    resolution : float, optional
        horizontal resolution to resample output raster to, by default 3
    resolution_units : str, optional
        unit for resolution, by default "Meters"
    dest_crs : str, optional
        Destination crs.

    Returns
    -------
    dict
        dictionary with paths to output rasters and scenario database
    """
    logging.info(f"create_fim_lib starting")
    nwm_rm = NwmReachModel(submodel_directory, library_directory)

    rm = RasManager(
        nwm_rm.ras_project_file,
        version=ras_version,
        terrain_path=nwm_rm.ras_terrain_hdf,
        crs=nwm_rm.crs,
    )

    for plan in plans:
        if f"{nwm_rm.model_name}_{plan}" not in rm.plans:
            logging.error(f"Plan {nwm_rm.model_name}_{plan} not found in the model, skipping...")
            continue
        else:
            post_process_depth_grids(
                rm,
                f"{nwm_rm.model_name}_{plan}",
                nwm_rm.fim_results_directory,
                accept_missing_grid=True,
                cog=cog,
                resolution=resolution,
                resolution_units=resolution_units,
                dest_crs=dest_crs,
            )

        if cleanup:
            shutil.rmtree(os.path.join(rm.ras_project._ras_dir, f"{nwm_rm.model_name}_{plan}"), ignore_errors=True)

    logging.info(f"create_fim_lib complete")

    return {"fim_results_directory": nwm_rm.fim_results_directory}
