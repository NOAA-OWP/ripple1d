"""Create HEC-RAS Terrains."""

from __future__ import annotations

import json
import logging
import os
import re
import traceback
from math import ceil, comb, pi
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import rioxarray
import xarray as xr
from pyproj import CRS
from shapely import LineString

from ripple1d.consts import (
    MAP_DEM_BUFFER_DIST_FT,
    MAP_DEM_UNCLIPPED_SRC_URL,
    MAP_DEM_VERT_UNITS,
    METERS_PER_FOOT,
    TERRAIN_AGREEMENT_PRECISION,
)
from ripple1d.data_model import XS, NwmReachModel
from ripple1d.errors import RasTerrainFailure
from ripple1d.ras import RasGeomText, create_terrain
from ripple1d.utils.dg_utils import clip_raster, reproject_raster
from ripple1d.utils.ripple_utils import fix_reversed_xs, resample_vertices, xs_concave_hull
from ripple1d.utils.sqlite_utils import export_terrain_agreement_metrics_to_db


def get_geometry_mask(gdf_xs_conc_hull: str, MAP_DEM_UNCLIPPED_SRC_URL: str) -> gpd.GeoDataFrame:
    """Get a geometry mask for the DEM based on the cross sections."""
    # build a DEM mask polygon based on the XS extents

    # Buffer the concave hull by transforming it to Albers, buffering it, then transforming it to the src raster crs
    with rasterio.open(MAP_DEM_UNCLIPPED_SRC_URL) as src:
        gdf_xs_conc_hull_buffered = (
            gdf_xs_conc_hull.to_crs(epsg=5070).buffer(MAP_DEM_BUFFER_DIST_FT * METERS_PER_FOOT).to_crs(src.crs)
        )

    if len(gdf_xs_conc_hull_buffered) != 1:
        raise ValueError(f"Expected 1 record in gdf_xs_conc_hull_buffered, got {len(gdf_xs_conc_hull_buffered)}")
    return gdf_xs_conc_hull_buffered.iloc[0]


def write_projection_file(crs: CRS, terrain_directory: str) -> str:
    """Write a projection file for the terrain."""
    projection_file = os.path.join(terrain_directory, "projection.prj")
    with open(projection_file, "w") as f:
        f.write(CRS(crs).to_wkt("WKT1_ESRI"))
    return projection_file


def create_ras_terrain(
    submodel_directory: str,
    terrain_source_url: str = MAP_DEM_UNCLIPPED_SRC_URL,
    vertical_units: str = MAP_DEM_VERT_UNITS,
    resolution: float = None,
    resolution_units: str = None,
    terrain_agreement_resolution: float = 3,
    terrain_agreement_format: str = "db",
    terrain_agreement_el_repeats: int = 5,
    terrain_agreement_el_ramp_rate: float = 2.0,
    terrain_agreement_el_init: float = 0.5,
    terrain_agreement_ignore_error: bool = True,
):
    """Create a RAS terrain file.

    Parameters
    ----------
    submodel_directory : str
        The path to the directory containing a submodel geopackage
    terrain_source_url : str, optional
        URL to a vrt or other raster with topographic data, by default
        MAP_DEM_UNCLIPPED_SRC_URL
    vertical_units : str, optional
        label for the vertical units of the source terrain (ex. Meters), by
        default MAP_DEM_VERT_UNITS
    resolution : float, optional
        horizontal resolution that terrain will be projected to, by default
        None
    resolution_units : str, optional
        unit for resolution parameter, by default None
    terrain_agreement_resolution : float, optional
        maximum distance allowed between the vertices used to calculate terrain
        agreement metrics (in units of resolution_units), by default 3
    terrain_agreement_format : str, optional
        whether to save the terrain agreement report as a json or a sqlite
        database, by default "db"
    terrain_agreement_el_repeats : int, optional
        how many times to repeat an elevation increment before ramping to the
        next elevation increment, by default 5
    terrain_agreement_el_ramp_rate : float, optional
        adjusts rate at which elevation increments increase after the designated
        number of repeats, by default 2.  The series of increments to be
        repeated is defined as inc_i = (ramp_rate^i)*initial_value.  A ramp rate
        of 2 will yield increments of 0.5, 1, 2, 4, 8, etc.  With a repeate
        value of 5, the series of increments will be 0.5, 0.5, 0.5, 0.5, 0.5,
        1.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 2.0, 4.0, etc.  That
        increment series will calculate agreement metrics at depths of 0.5, 1.0,
        1.5, 2.0, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 9.5, 11.5, 13.5, 15.5, 17.5,
        21.5, etc.
    terrain_agreement_el_init : float, optional
        initial value for terrain agreement elevation increments, by default 0.5
    terrain_agreement_ignore_error : bool, optional
        whether to raise or log+ignore any errors encountered in the compute_terrain_agreement_metrics function.
    task_id : str, optional
        Task ID to use for logging, by default ""

    Returns
    -------
    str
        descriptive string detailing where terrain was saved

    Raises
    ------
    ValueError
        Raised when resolution arg has been provided but resolution_units arg
        has not been provided
    ValueError
        Raised when resolution_units are not Feet or Meters
    FileNotFoundError
        Raised when no geopackage file is found in submodel_directory

    Notes
    -----
    The create_ras_terrain endpoint downloads a digital elevation model (DEM)
    for the modeled area and exports it to a HEC-RAS terrain file.  DEM data
    may be sourced from any virtual raster source, but the default source is
    the `USGS 3DEP 1/3 arcsecond dataset
    <https://www.sciencebase.gov/catalog/item/4f70aa9fe4b058caae3f8de5>`_.  By
    default, terrain data is clipped to a 1,000-foot buffer around a concave
    hull of the submodel cross-sections, however, the buffer distance may be
    adjusted.  If resolution data is passed to the endpoint, the terrain raster
    will be resampled to that resolution.
    """
    logging.info(f"create_ras_terrain starting")

    if resolution and not resolution_units:
        raise ValueError(
            f"'resolution' arg has been provided but 'resolution_units' arg has not been provided. Please provide both"
        )

    if resolution_units:
        if resolution_units not in ["Feet", "Meters"]:
            raise ValueError(f"invalid resolution_units: {resolution_units}. expected 'Feet' or 'Meters'")

    nwm_rm = NwmReachModel(submodel_directory)

    if not nwm_rm.file_exists(nwm_rm.ras_gpkg_file):
        raise FileNotFoundError(f"NwmReachModel class expecting ras_gpkg_file {nwm_rm.ras_gpkg_file}, file not found")

    if not os.path.exists(nwm_rm.terrain_directory):
        os.makedirs(nwm_rm.terrain_directory, exist_ok=True)

    mask = get_geometry_mask(nwm_rm.xs_concave_hull, terrain_source_url)

    # clip dem
    src_dem_clipped_localfile = os.path.join(nwm_rm.terrain_directory, "temp.tif")
    map_dem_clipped_basename = os.path.basename(terrain_source_url)
    src_dem_reprojected_localfile = os.path.join(
        nwm_rm.terrain_directory, map_dem_clipped_basename.replace(".vrt", ".tif")
    )

    clip_raster(
        terrain_source_url,
        src_dem_clipped_localfile,
        mask,
        vertical_units,
    )

    # reproject/resample dem
    logging.debug(f"Reprojecting/Resampling DEM {src_dem_clipped_localfile} to {src_dem_clipped_localfile}")
    reproject_raster(
        src_dem_clipped_localfile, src_dem_reprojected_localfile, CRS(nwm_rm.crs), resolution, resolution_units
    )
    os.remove(src_dem_clipped_localfile)

    # write projection file
    projection_file = write_projection_file(nwm_rm.crs, nwm_rm.terrain_directory)

    # Make the RAS mapping terrain locally
    result = create_terrain(
        [src_dem_reprojected_localfile],
        projection_file,
        f"{nwm_rm.terrain_directory}\\{nwm_rm.model_name}",
    )
    terrain_path = result["RAS Terrain"] + "." + map_dem_clipped_basename.replace(".vrt", ".tif")
    if os.path.exists(terrain_path):
        os.remove(src_dem_reprojected_localfile)
    else:
        raise RasTerrainFailure(f"Tried to create terrain at {terrain_path}, but CreateTerrain exe failed.")
    nwm_rm.update_write_ripple1d_parameters({"source_terrain": terrain_source_url})
    logging.info(f"create_ras_terrain complete")

    # Calculate terrain agreement metrics
    try:
        agreement_path = compute_terrain_agreement_metrics(
            submodel_directory,
            terrain_path,
            terrain_agreement_resolution,
            resolution_units,
            terrain_agreement_format,
            terrain_agreement_el_repeats,
            terrain_agreement_el_ramp_rate,
            terrain_agreement_el_init,
        )
        result["terrain_agreement"] = agreement_path
    except Exception as e:
        if not terrain_agreement_ignore_error:
            raise e
        logging.error(f"| Error: {e}")
        logging.error(f"| Traceback: {traceback.format_exc()}")

        result["terrain_agreement"] = None
    return result


### Terrain Agreement Metrics ###


def compute_terrain_agreement_metrics(
    submodel_directory: str,
    dem_path: str,
    terrain_agreement_resolution: float = 3,
    horizontal_units: str = None,
    terrain_agreement_format: str = "db",
    terrain_agreement_el_repeats: int = 5,
    terrain_agreement_el_ramp_rate: float = 2.0,
    terrain_agreement_el_init: float = 0.5,
):
    """Compute a suite of agreement metrics between source model XS data and mapping DEM."""
    # Load model information
    nwm_rm = NwmReachModel(submodel_directory)

    # Add DEM data to geom object
    geom = RasGeomText.from_gpkg(nwm_rm.derive_path(".gpkg"), "", "")
    section_data = sample_terrain(geom, dem_path, terrain_agreement_resolution, horizontal_units)

    # Compute agreement metrics
    metrics = geom_agreement_metrics(
        section_data, terrain_agreement_el_repeats, terrain_agreement_el_ramp_rate, terrain_agreement_el_init
    )

    # Save results and summary
    metric_path = export_agreement_metrics(
        nwm_rm.terrain_agreement_file(terrain_agreement_format), metrics, terrain_agreement_format
    )
    nwm_rm.update_write_ripple1d_parameters({"terrain_agreement_summary": metrics["summary"]})
    return metric_path


def interpolater(coords: np.ndarray, stations: np.ndarray) -> np.ndarray:
    """Interpolate faster than shapely linestring."""
    x = coords[:, 0]
    y = coords[:, 1]
    dists = np.cumsum(np.sqrt((np.diff(x, 1) ** 2) + (np.diff(y, 1) ** 2)))
    dists = np.insert(dists, 0, 0)
    newx = np.interp(stations, dists, x)
    newy = np.interp(stations, dists, y)
    return newx, newy


def sample_terrain(geom: RasGeomText, dem_path: str, max_interval: float = 3, horizontal_units: str = None):
    """Add DEM station_elevations to cross-sections."""
    # Align section units and user units
    xs_units = geom.cross_sections[next(iter(geom.cross_sections))].crs_units
    if horizontal_units is None:
        pass
    elif xs_units in ["US survey foot", "foot"] and horizontal_units == "Feet":
        pass
    elif xs_units == "metre" and horizontal_units == "Meters":
        pass
    elif xs_units == "metre" and horizontal_units == "Feet":
        max_interval /= 3.281
    elif xs_units in ["US survey foot", "foot"] and horizontal_units == "Meters":
        max_interval *= 3.281
    else:
        raise ValueError(
            f"Error aligning XS units to user-supplied units.  xs.crs_units={xs_units} and user supplied {horizontal_units}"
        )

    # Sample terrain
    section_data = {}
    with rioxarray.open_rasterio(dem_path) as dem:
        for section in geom.cross_sections:
            station_elevation_points = np.array(geom.cross_sections[section].station_elevation_points)
            stations = station_elevation_points[:, 0]
            stations = resample_vertices(stations, max_interval)
            rel_stations = stations - stations.min()

            xs, ys = interpolater(np.array(geom.cross_sections[section].coords), rel_stations)
            tgt_x = xr.DataArray(xs, dims="points")
            tgt_y = xr.DataArray(ys, dims="points")
            el = dem.sel(band=1, x=tgt_x, y=tgt_y, method="nearest").values
            dem_xs = np.column_stack((stations, el))
            src_resampled = np.interp(stations, station_elevation_points[:, 0], station_elevation_points[:, 1])
            src_xs = np.column_stack((stations, src_resampled))
            section_data[section] = {"dem_xs": dem_xs, "src_xs": src_xs}
    return section_data


def geom_agreement_metrics(
    xs_data: dict,
    terrain_agreement_el_repeats: int = 5,
    terrain_agreement_el_ramp_rate: float = 2.0,
    terrain_agreement_el_init: float = 0.5,
) -> dict:
    """Compute a suite of agreement metrics between source model XS data and a sampled DEM."""
    metrics = {"xs_metrics": {}, "summary": {}}
    for section in xs_data:
        metrics["xs_metrics"][section] = xs_agreement_metrics(
            xs_data[section], terrain_agreement_el_repeats, terrain_agreement_el_ramp_rate, terrain_agreement_el_init
        )

    # aggregate
    metrics["model_metrics"] = summarize_dict(
        {i: metrics["xs_metrics"][i]["summary"] for i in metrics["xs_metrics"]}
    )  # Summarize summaries
    del metrics["model_metrics"]["max_el_residuals"]  # Averages are not applicable here

    return round_values(metrics)


def export_agreement_metrics(out_path: str, metrics: dict, f: str = "db"):
    """Save the terrain agreement metrics to a json or db."""
    if os.path.exists(out_path):
        os.remove(out_path)

    if f == "json":
        with open(out_path, "w") as f:
            json.dump(metrics, f, indent=4)
    elif f == "db":
        export_terrain_agreement_metrics_to_db(out_path, metrics)
    else:
        raise ValueError(
            f"Tried exporting terrain agreement metrics to format={f}, but only db (sqlite) and json are supported"
        )
    return out_path


def xs_agreement_metrics(
    xs: XS,
    terrain_agreement_el_repeats: int = 5,
    terrain_agreement_el_ramp_rate: float = 2.0,
    terrain_agreement_el_init: float = 0.5,
) -> dict:
    """Compute a suite of agreement metrics between source model XS data and mapping DEM."""
    metrics = {}

    # Elevation-specific metrics and their summaries
    metrics["xs_elevation_metrics"] = variable_metrics(
        xs["src_xs"],
        xs["dem_xs"],
        terrain_agreement_el_repeats,
        terrain_agreement_el_ramp_rate,
        terrain_agreement_el_init,
    )
    metrics["summary"] = summarize_dict(metrics["xs_elevation_metrics"])

    # Whole XS metrics
    src_xs_el = xs["src_xs"][:, 1]
    dem_xs_el = xs["dem_xs"][:, 1]
    metrics["summary"]["r_squared"] = r_squared(src_xs_el, dem_xs_el)
    metrics["summary"]["spectral_angle"] = spectral_angle(src_xs_el, dem_xs_el)
    metrics["summary"]["spectral_correlation"] = spectral_correlation(src_xs_el, dem_xs_el)
    metrics["summary"]["correlation"] = correlation(src_xs_el, dem_xs_el)
    metrics["summary"]["max_cross_correlation"] = cross_correlation(src_xs_el, dem_xs_el)
    metrics["summary"]["thalweg_elevation_difference"] = thalweg_elevation_difference(src_xs_el, dem_xs_el)

    return metrics


def round_values(in_dict: dict) -> dict:
    """Round values to keep precision consistent (recursive)."""
    for k, v in in_dict.items():
        if isinstance(v, float):
            in_dict[k] = round(v, TERRAIN_AGREEMENT_PRECISION[k])
        elif isinstance(v, dict):
            in_dict[k] = round_values(v)
        else:
            in_dict[k] = None  # at the moment, we have no other value types
    return in_dict


def summarize_dict(in_dict: dict) -> dict:
    """Aggregate metrics from a dictionary."""
    summary = {}
    keys = list(in_dict.keys())
    submetrics = list(in_dict[keys[0]].keys())
    for m in submetrics:
        if isinstance(in_dict[keys[0]][m], float):  # average floats
            out_key = m if m.startswith("avg_") else f"avg_{m}"
            summary[out_key] = sum([in_dict[k][m] for k in keys]) / len(keys)
        elif isinstance(in_dict[keys[0]][m], dict):  # Provide last entry for dicts
            out_key = m if m.startswith("max_el_") else f"max_el_{m}"
            summary[out_key] = in_dict[keys[-1]][m]
    return summary


def residual_metrics(residuals: np.ndarray) -> dict:
    """Summary statistics on residuals between two arrays."""
    metrics = {}
    metrics["mean"] = residuals.mean()
    metrics["std"] = residuals.std()
    metrics["max"] = residuals.max()
    metrics["min"] = residuals.min()
    metrics["p_25"] = np.percentile(residuals, 25)
    metrics["p_50"] = np.percentile(residuals, 50)
    metrics["p_75"] = np.percentile(residuals, 75)
    metrics["rmse"] = np.sqrt((residuals * residuals).mean())
    metrics["normalized_rmse"] = metrics["rmse"] / (metrics["p_75"] - metrics["p_25"])
    return metrics


def ss(x: np.ndarray, y: np.ndarray) -> float:
    """Calculate the sum of squares."""
    return np.sum((x - np.mean(x)) * (y - np.mean(y)))


def r_squared(a1: np.ndarray, a2: np.ndarray) -> float:
    """Calculate R-squared for two series."""
    return ss(a1, a2) ** 2 / (ss(a1, a1) * ss(a2, a2))


def spectral_angle(a1: np.ndarray, a2: np.ndarray, norm: bool = True) -> float:
    """Calculate the spectral angle of two vectors.

    Following the approach of https://github.com/BYU-Hydroinformatics/HydroErr/blob/42a84f3e006044f450edc7393ed54d59f27ef35b/HydroErr/HydroErr.py#L3541
    """
    a = np.dot(a1, a2)
    b = np.linalg.norm(a1) * np.linalg.norm(a2)
    sa = np.arccos(a / b)
    if norm:
        sa = 1 - (abs(sa) / (pi / 2))
    return sa


def spectral_correlation(a1: np.ndarray, a2: np.ndarray, norm: bool = True) -> float:
    """Calculate the spectral angle of two vectors.

    Following the approach of https://github.com/BYU-Hydroinformatics/HydroErr/blob/42a84f3e006044f450edc7393ed54d59f27ef35b/HydroErr/HydroErr.py#L3541
    """
    a = np.dot(a1 - np.mean(a1), a2 - np.mean(a2))
    b = np.linalg.norm(a1 - np.mean(a1))
    c = np.linalg.norm(a2 - np.mean(a2))
    e = b * c
    if abs(a / e) > 1:
        sc = 0
    else:
        sc = np.arccos(a / e)
    if norm:
        sc = 1 - (abs(sc) / (pi / 2))
    return sc


def correlation(a1: np.ndarray, a2: np.ndarray) -> float:
    """Calculate Pearson's correlation of two series."""
    num = np.sum((a1 - a1.mean()) * (a2 - a2.mean()))
    denom = np.sqrt(np.sum(np.square(a1 - a1.mean())) * np.sum(np.square(a2 - a2.mean())))
    return num / denom


def cross_correlation(a1: np.ndarray, a2: np.ndarray) -> float:
    """Calculate the maximum cross-correlation between two time series."""
    a1_detrend = a1 - a1.mean()
    a2_detrend = a2 - a2.mean()
    cross_corr = np.correlate(a1_detrend, a2_detrend, mode="full")
    norm_factor = np.sqrt(np.sum(a1_detrend**2) * np.sum(a2_detrend**2))
    cross_corr /= norm_factor
    return cross_corr.max()


def thalweg_elevation_difference(a1: np.ndarray, a2: np.ndarray) -> float:
    """Calculate the elevation difference between the thalweg (low point) of two series."""
    return a1.min() - a2.min()


def variable_metrics(
    src_xs: np.ndarray,
    dem_xs: np.ndarray,
    terrain_agreement_el_repeats: int = 5,
    terrain_agreement_el_ramp_rate: float = 2.0,
    terrain_agreement_el_init: float = 0.5,
) -> dict:
    """Calculate metrics for WSE values every half foot."""
    all_metrics = {}
    residuals = src_xs - dem_xs  # Calculate here to save compute
    for wse in get_wses(
        src_xs, terrain_agreement_el_repeats, terrain_agreement_el_ramp_rate, terrain_agreement_el_init
    ):
        tmp_src_xs = add_intersection_pts(src_xs, wse)
        tmp_dem_xs = add_intersection_pts(dem_xs, wse)
        metrics = {}
        metrics["inundation_overlap"] = inundation_agreement(src_xs, dem_xs, wse)
        metrics["flow_area_overlap"] = flow_area_overlap(src_xs, dem_xs, wse)
        metrics["top_width_agreement"] = top_width_agreement(tmp_src_xs, tmp_dem_xs, wse)
        metrics["flow_area_agreement"] = flow_area_agreement(tmp_src_xs, tmp_dem_xs, wse)
        metrics["hydraulic_radius_agreement"] = hydraulic_radius_agreement(tmp_src_xs, tmp_dem_xs, wse)
        resid_mask = (src_xs[:, 1] < wse) | (dem_xs[:, 1] < wse)
        metrics["residuals"] = residual_metrics(residuals[resid_mask])
        all_metrics[wse] = metrics
    return all_metrics


def get_wses(
    xs: np.ndarray,
    terrain_agreement_el_repeats: int = 5,
    terrain_agreement_el_ramp_rate: float = 2.0,
    terrain_agreement_el_init: float = 0.5,
) -> list[float]:
    """Derive grid of water surface elevations from minimum el to lowest cross-section endpoint."""
    start_el = xs[:, 1].min()
    start_el = ceil(start_el / terrain_agreement_el_init) * terrain_agreement_el_init  # Round to nearest init_inc
    if abs(start_el - xs[:, 1].min()) < terrain_agreement_el_init:  # Sometimes rounding error will set these equal.
        start_el += terrain_agreement_el_init
    end_el = min((xs[0, 1], xs[-1, 1]))
    end_el = ceil(end_el / terrain_agreement_el_init) * terrain_agreement_el_init  # Round to nearest init_inc
    end_el = max(
        end_el, start_el
    )  # If section was poorly created with endpoints not on high ground, get at least one metric.

    increments = np.arange(0, 10, 1)
    increments = (terrain_agreement_el_ramp_rate**increments) * terrain_agreement_el_init
    increments = np.repeat(increments, terrain_agreement_el_repeats)
    increments = np.cumsum(increments) - terrain_agreement_el_init

    series = increments + start_el
    if series[-1] < end_el:
        series = np.append(series, end_el)
    else:
        series = series[series <= end_el]
    return np.round(series, 1)


def add_intersection_pts(section_pts: np.ndarray, wse: float) -> np.ndarray:
    """Add points to XS where WSE hits."""
    intersection = wse_intersection_pts(section_pts, wse)
    if len(intersection) == 0:
        return section_pts
    inds = np.searchsorted(section_pts[:, 0], intersection[:, 0])
    return np.insert(section_pts, inds, intersection, axis=0)


def wse_intersection_pts(section_pts: np.ndarray, wse: float) -> list[tuple[float]]:
    """Find where the cross-section terrain intersects the water-surface elevation."""
    intersection_pts = []

    # Iterate through all pairs of points and find any points where the line would cross the wse
    for i in range(len(section_pts) - 1):
        p1 = section_pts[i]
        p2 = section_pts[i + 1]

        if p1[1] > wse and p2[1] > wse:  # No intesection
            continue
        elif p1[1] < wse and p2[1] < wse:  # Both below wse
            continue
        elif p1[1] == wse or p2[1] == wse:  # already vertex present
            continue

        # Define line
        m = (p2[1] - p1[1]) / (p2[0] - p1[0])
        b = p1[1] - (m * p1[0])

        # Find intersection point with Cramer's rule
        determinant = lambda a, b: (a[0] * b[1]) - (a[1] * b[0])
        div = determinant((1, 1), (-m, 0))
        tmp_y = determinant((b, wse), (-m, 0)) / div
        tmp_x = determinant((1, 1), (b, wse)) / div

        intersection_pts.append((tmp_x, tmp_y))
    return np.array(intersection_pts)


def smape_series(a1: np.ndarray, a2: np.ndarray) -> float:
    """Return the symmetric mean absolute percentage errror of two series."""
    num = np.abs(a1 - a2)
    denom = np.abs(a1) + np.abs(a2)
    return (np.sum(num / denom)) / len(a1)


def smape_single(a1: float, a2: float) -> float:
    """Return the symmetric mean absolute percentage errror of two values."""
    if a1 == a2:
        return 0.0  # handles zero denominator
    return abs(a1 - a2) / (abs(a1) + abs(a2))


def inundation_agreement(src_el: np.ndarray, dem_el: np.ndarray, wse: float) -> float:
    """Calculate the percent of the cross-section with agreeing wet/dry."""
    dx = np.diff(src_el[:, 0], 1)

    src_wet = src_el[:, 1] < wse
    src_wet = src_wet[1:] | src_wet[:-1]
    dem_wet = dem_el[:, 1] < wse
    dem_wet = dem_wet[1:] | dem_wet[:-1]
    agree = src_wet & dem_wet
    total = src_wet | dem_wet
    return np.sum(agree * dx) / np.sum(total * dx)


def flow_area_overlap(src_el: np.ndarray, dem_el: np.ndarray, wse: float) -> float:
    """Calculate the percent of unioned flow area that agree."""
    dx = np.diff(src_el[:, 0], 1)

    src_depths = np.clip(wse - src_el[:, 1], 0, None)
    src_areas = ((src_depths[1:] + src_depths[:-1]) / 2) * dx

    dem_depths = np.clip(wse - dem_el[:, 1], 0, None)
    dem_areas = ((dem_depths[1:] + dem_depths[:-1]) / 2) * dx

    combo = np.column_stack([src_areas, dem_areas])
    agree = np.min(combo, axis=1)
    max_area = np.max(combo, axis=1)
    return np.sum(agree) / np.sum(max_area)


def top_width_agreement(src_el: np.ndarray, dem_el: np.ndarray, wse: float) -> float:
    """Calculate the agreement of the wetted top widths."""
    src_tw = get_wetted_top_width(src_el, wse)
    dem_tw = get_wetted_top_width(dem_el, wse)
    return 1 - smape_single(src_tw, dem_tw)


def flow_area_agreement(src_el: np.ndarray, dem_el: np.ndarray, wse: float) -> float:
    """Calculate the agreement of the wetted top widths."""
    src_area = get_flow_area(src_el, wse)
    dem_area = get_flow_area(dem_el, wse)
    return 1 - smape_single(src_area, dem_area)


def hydraulic_radius_agreement(src_el: np.ndarray, dem_el: np.ndarray, wse: float) -> float:
    """Calculate the agreement of the hydraulic radii."""
    src_radius = get_hydraulic_radius(src_el, wse)
    dem_radius = get_hydraulic_radius(dem_el, wse)
    return 1 - smape_single(src_radius, dem_radius)


def get_wetted_top_width(station_elevation_series: np.ndarray, wse: float) -> float:
    """Derive wetted-top-width for a given stage."""
    dx = np.diff(station_elevation_series[:, 0], 1)
    wet = station_elevation_series[:, 1] < wse
    wet = wet[1:] | wet[:-1]
    return np.sum(dx * wet)


def get_flow_area(station_elevation_series: np.ndarray, wse: float) -> float:
    """Derive wetted-top-width for a given stage."""
    depths = np.clip(wse - station_elevation_series[:, 1], 0, None)
    return np.trapezoid(depths, station_elevation_series[:, 0])


def get_wetted_perimeter(station_elevation_series: np.ndarray, wse: float) -> float:
    """Derive wetted-perimeter for a given stage."""
    station_elevation_series = station_elevation_series[station_elevation_series[:, 1] < wse]
    diffs = np.diff(station_elevation_series, axis=0)
    return np.sum(np.sqrt(np.square(diffs[:, 0]) + np.square(diffs[:, 1])))


def get_hydraulic_radius(station_elevation_series: np.ndarray, wse: float) -> float:
    """Derive wetted-perimeter for a given stage."""
    wp = get_wetted_perimeter(station_elevation_series, wse)
    if wp == 0:
        return 0
    a = get_flow_area(station_elevation_series, wse)
    return a / wp
