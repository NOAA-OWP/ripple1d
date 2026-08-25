"""Utils for working with raster data."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Tuple

import numpy as np
import pystac
import rasterio
import rasterio.warp
import shapely
import shapely.ops
from mypy_boto3_s3.service_resource import Object
from pyproj import CRS
from rasterio import mask
from rasterio.session import AWSSession
from rasterio.warp import Resampling, calculate_default_transform, reproject
from shapely import Polygon

from ripple1d.consts import METERS_PER_FOOT
from ripple1d.errors import NullTerrainError, UnknownVerticalUnits

from .s3_utils import *

logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)


def get_raster_bounds(raster_file: str) -> Tuple[float, float, float, float]:
    """
    Retrieve the geographic bounds of a raster file and returns them in the WGS 84 (EPSG:4326) coordinate reference system.

    Parameters
    ----------
        raster_file (str): The raster file.

    Returns
    -------
        Tuple[float, float, float, float]: The geographic bounds of the raster file in the WGS 84 (EPSG:4326) coordinate reference system. The bounds are returned as a tuple of four floats: (west, south, east, north).
    """
    with rasterio.open(raster_file) as src:
        bounds = src.bounds
        crs = src.crs
        bounds_4326 = rasterio.warp.transform_bounds(crs, "EPSG:4326", *bounds)
        return bounds_4326


def get_raster_metadata(raster_file: str) -> dict:
    """
    Retrieve the metadata of a raster file.

    Parameters
    ----------
        raster_file (str): The raster file.

    Returns
    -------
        dict: The metadata of the raster file. The metadata is returned as a dictionary
        where the keys are the names of the metadata items and the values are the values of the metadata items.
    """
    with rasterio.open(raster_file) as src:
        return src.tags(1)


def bbox_to_polygon(bbox) -> shapely.Polygon:
    """
    Convert a bounding box to a Shapely Polygon.

    Parameters
    ----------
        bbox: The bounding box to convert. It should be a sequence of four numbers: (min_x, min_y, max_x, max_y).

    Returns
    -------
        shapely.Polygon: The Shapely Polygon representing the bounding box. The Polygon is a rectangle with the lower
          left corner at (min_x, min_y) and the upper right corner at (max_x, max_y).
    """
    min_x, min_y, max_x, max_y = bbox
    return shapely.Polygon(
        [
            [min_x, min_y],
            [min_x, max_y],
            [max_x, max_y],
            [max_x, min_y],
        ]
    )


def get_unit_name(crs: CRS):
    """Get units from a crs object."""
    unit_name = crs.axis_info[0].unit_name
    english = ["ft", "feet", "foot"]
    metric = ["m", "meter", "meters"]
    for name in english:
        if name in unit_name.lower():
            return "Feet"
    for name in metric:
        if name in unit_name.lower():
            return "Meters"
    raise ValueError(f"unrecognized units ")


def convert_units(dst_crs: CRS, resolution: float, resolution_units: str) -> float:
    """Convert resolution to match the units of the destination crs."""
    dest_units = get_unit_name(dst_crs)
    if dest_units != resolution_units:
        if resolution_units == "Feet" and dest_units == "Meters":
            resolution = resolution * METERS_PER_FOOT
        elif resolution_units == "Meters" and dest_units == "Feet":
            resolution = resolution / METERS_PER_FOOT
    return resolution


def reproject_raster(
    src_path: str,
    dest_path: str,
    dst_crs: CRS,
    resolution: float = None,
    resolution_units: str = None,
    compress="DEFLATE",
    predictor="3",
    num_threads=4,
    tiled=False,
    blocksize=512,
):
    """Reproject/resample raster."""
    with rasterio.open(src_path) as src:
        if not resolution and not resolution_units:
            resolution = src.res[0]
            transform, width, height = calculate_default_transform(src.crs, dst_crs, src.width, src.height, *src.bounds)
        else:
            resolution = convert_units(dst_crs, resolution, resolution_units)
            transform, width, height = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds, resolution=resolution
            )
        kwargs = src.meta.copy()
        kwargs.update({"crs": dst_crs, "transform": transform, "width": width, "height": height})

        kwargs.update(
            {
                "blockysize": blocksize,
                "blockxsize": blocksize,
                "tiled": tiled,
                "COMPRESS": compress,
                "PREDICTOR": predictor,
                "num_threads": num_threads,
            }
        )
        with rasterio.open(dest_path, "w", **kwargs) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    dst_resolution=resolution,
                    resampling=Resampling.nearest,
                    kwarg=kwargs,
                )


def clip_raster(src_path: str, dst_path: str, mask_polygon: Polygon, vertical_units: str):
    """Clip a raster file to a polygon and save the result to a new file."""
    if os.path.exists(dst_path):
        raise FileExistsError(dst_path)
    if not isinstance(mask_polygon, Polygon):
        raise TypeError(mask_polygon)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)

    logging.info(f"Reading: {src_path}")
    with rasterio.open(src_path) as src:
        out_meta = src.meta
        out_image, out_transform = mask.mask(src, [mask_polygon], all_touched=True, crop=True)
        nd = src.nodata

    if np.all(out_image == nd):
        raise NullTerrainError(f"Terrain downloaded from {src_path} was all nodata values.")

    out_meta.update(
        {
            "driver": "GTiff",
            "height": out_image.shape[1],
            "width": out_image.shape[2],
            "transform": out_transform,
            "compress": "LZW",
            "predictor": 3,
            "tiled": True,
        }
    )

    logging.info(f"Writing as masked: {dst_path}")
    with rasterio.open(dst_path, "w", **out_meta) as dest:
        if vertical_units == "Meters":
            out_image_ft = np.where(out_image == out_meta["nodata"], out_meta["nodata"], out_image / METERS_PER_FOOT)
            dest.write(out_image_ft)
        elif vertical_units == "Feet":
            dest.write(out_image)
        else:
            raise UnknownVerticalUnits(f"Expected Feet or Meters recieved {vertical_units}")


def get_terrain_exe_path(ras_ver: str) -> str:
    """Return Windows path to RasProcess.exe exposing CreateTerrain subroutine, compatible with provided RAS version."""
    # 5.0.7 version of RasProcess.exe does not expose CreateTerrain subroutine.
    # Testing shows that RAS 5.0.7 accepts Terrain created by 6.1 version of RasProcess.exe, so use that for 5.0.7.
    d = {
        "507": r"C:\Program Files (x86)\HEC\HEC-RAS\6.1\RasProcess.exe",
        "5.07": r"C:\Program Files (x86)\HEC\HEC-RAS\6.1\RasProcess.exe",
        "600": r"C:\Program Files (x86)\HEC\HEC-RAS\6.0\RasProcess.exe",
        "6.00": r"C:\Program Files (x86)\HEC\HEC-RAS\6.0\RasProcess.exe",
        "610": r"C:\Program Files (x86)\HEC\HEC-RAS\6.1\RasProcess.exe",
        "6.10": r"C:\Program Files (x86)\HEC\HEC-RAS\6.1\RasProcess.exe",
        "631": r"C:\Program Files (x86)\HEC\HEC-RAS\6.3.1\RasProcess.exe",
        "6.3.1": r"C:\Program Files (x86)\HEC\HEC-RAS\6.3.1\RasProcess.exe",
    }
    try:
        return d[ras_ver]
    except KeyError as e:
        raise ValueError(f"Unsupported ras_ver: {ras_ver}. choices: {sorted(d)}") from e
