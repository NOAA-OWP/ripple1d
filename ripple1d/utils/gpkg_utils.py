"""Utils for working with geopackages."""

import json
import logging
import os
import re
import subprocess
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict

import boto3
import contextily as ctx
import fiona
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import pystac
import requests
from pyproj import CRS
from shapely import Polygon, to_geojson

from ripple1d.consts import LAYER_COLORS
from ripple1d.data_model import NwmReachModel, RasModelStructure
from ripple1d.errors import UnkownCRSUnitsError
from ripple1d.ras import (
    VALID_GEOMS,
    VALID_PLANS,
    VALID_STEADY_FLOWS,
    RasFlowText,
    RasGeomText,
    RasManager,
    RasPlanText,
    RasProject,
)
from ripple1d.utils.s3_utils import get_basic_object_metadata, init_s3_resources, str_from_s3


def get_river_miles(river_gdf: gpd.GeoDataFrame):
    """Compute the total length of the river centerlines in miles."""
    if "units" not in river_gdf.crs.to_dict().keys():
        raise UnkownCRSUnitsError("No units specified. The coordinate system may be Geographic.")
    units = river_gdf.crs.to_dict()["units"]
    if units in ["ft-us", "ft", "us-ft"]:
        conversion_factor = 1 / 5280
    elif units in ["m", "meters"]:
        conversion_factor = 1 / 1609
    else:
        raise UnkownCRSUnitsError(f"Expected feet or meters; got: {units}")
    return round(river_gdf.length.sum() * conversion_factor, 2)


def gpkg_to_geodataframe(gpkg_s3_uri: str) -> dict:
    """
    Convert a local geopackage file to a GeoDataFrame.

    Parameters
    ----------
        gpkg_key (str): Path of locally saved geopackage.

    Returns
    -------
        gpkg_gdf (dict): dictionary of GeoDataFrame.

    """
    layers = fiona.listlayers(gpkg_s3_uri)
    gdfs = {}
    for layer in layers:
        gdfs[layer] = gpd.read_file(gpkg_s3_uri, layer=layer)

    return gdfs


def reproject(gdfs: dict, crs=4326) -> dict:
    """Reproject a gdf to a new CRS."""
    for layer, gdf in gdfs.items():
        gdfs[layer] = gdf.to_crs(crs)  # reproject to WSG 84
    return gdfs


def create_thumbnail_from_gpkg(gdfs: dict) -> plt.Figure:
    """
    Create a figure displaying the geopandas dataframe provided in the gdfs dictionary.

    Parameters
    ----------
    - gdf (dict): A dictionary of geopandas dataframes containing the geometries to plot.
    """
    # Define colors for each layer type

    # Plotting
    fig, ax = plt.subplots(1, 1, figsize=(10, 10))

    for layer, color in LAYER_COLORS.items():
        if layer in gdfs.keys():
            gdfs[layer].plot(ax=ax, color=color, linewidth=1, label=layer)
            crs = gdfs[layer].crs

    # Add openstreetmap basemap
    try:
        ctx.add_basemap(ax, crs=crs, source=ctx.providers.USGS.USTopo)
    except requests.exceptions.HTTPError as e:
        try:
            ctx.add_basemap(ax, crs=crs, source=ctx.providers.Esri.WorldStreetMap)
        except requests.exceptions.HTTPError as e:
            ctx.add_basemap(ax, crs=crs, source=ctx.providers.OpenStreetMap.Mapnik)
    ax.legend()

    # Hide all axis text ticks or tick labels
    ax.set_xticks([])
    ax.set_yticks([])
    return fig


def write_thumbnail_to_s3(fig: plt.Figure, png_s3_key: str, bucket: str, s3_client: boto3.Session.client):
    """
    Write a PNG thumbnail to AWS S3.

    Parameters
    ----------
    - fig (plt.Figure): The figure to export to png on s3.
        - png_s3_key (str): The S3 path where the generated PNG thumbnail is to be stored.
    - bucket (str): The S3 bucket
    - s3_client: The AWS S3 client instance used for uploading the PNG.
    """
    # Save plot to a bytes buffer
    buf = BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()

    # Download the PNG to s3
    s3_client.put_object(Bucket=bucket, Key=png_s3_key, Body=buf, ContentType="image/png")


def create_geom_item(
    item_id: str,
    bbox: Polygon,
    footprint: list[float],
    properties: dict,
) -> pystac.Item:
    """
    Create a PySTAC Item for a gpkg file stored in an AWS S3 bucket.

    Parameters
    ----------
        item_id (str): item_id.
        bbox (shapely.Polygon): Item bounding box.
        footprint (list[float]): Item Footprint.

    Returns
    -------
        pystac.Item: The PySTAC Item representing the gpkg file.
    """
    # TODO: Adjust start_time selection for item
    start_time = datetime.utcnow()
    item = pystac.Item(
        id=item_id,
        geometry=json.loads(to_geojson(footprint)),
        bbox=bbox.tolist(),
        datetime=start_time,
        properties=properties,
    )

    return item


def get_asset_info(asset_key: str, rms: RasModelStructure, bucket: str = None) -> dict:
    """
    Generate information for an asset based on its file extension.

    Parameters
    ----------
        asset_key (str): The S3 key of the asset.
        rms (str): The RasModelStructure.
        bucket (str): The S3 bucket where the asset is stored.

    Returns
    -------
        dict: A dictionary with the roles, the description, and the title of the asset.
    """
    if bucket:
        _, client, s3_resource = init_s3_resources()
        obj = s3_resource.Bucket(bucket).Object(asset_key)
        extra_fields = get_basic_object_metadata(obj)
    else:
        extra_fields = {"file:size": os.path.getsize(asset_key), "last_modified": os.path.getmtime(asset_key)}

    file_extension = Path(asset_key).suffix
    title = Path(asset_key).name.replace(" ", "_")
    if file_extension in VALID_PLANS + VALID_GEOMS + VALID_STEADY_FLOWS:
        if bucket:
            string = str_from_s3(rms.ras_project_file, client, bucket)
            rp = RasProject.from_str(string, rms.ras_project_file)
        else:
            rm = RasManager(rms.ras_project_file, crs=rms.crs)

    description = ""
    roles = []

    if re.match(".f[0-9]{2}", file_extension):
        roles.extend(["forcing", "hec-ras", pystac.MediaType.TEXT])
        description = """Forcing file for ras."""
        if bucket:
            string = str_from_s3(asset_key, client, bucket)
            flow = RasFlowText.from_str(string, " .f01")
        else:
            flow = [i for i in rm.flows.values() if i.file_extension == file_extension][0]
        extra_fields["Title"] = flow.title
        extra_fields["Number of Profiles"] = flow.n_profiles
        extra_fields["Profile Names"] = flow.profile_names

    elif re.match(".g[0-9]{2}", file_extension):
        roles.extend(["geometry", "hec-ras", pystac.MediaType.TEXT])
        description = """Geometry file for ras."""
        if bucket:
            string = str_from_s3(asset_key, client, bucket)
            geom = RasGeomText.from_str(string, rms.crs, " .g01")
        else:
            geom = [i for i in rm.geoms.values() if i.file_extension == file_extension][0]
        extra_fields["Title"] = geom.title
        extra_fields["Number of rivers"] = geom.n_rivers
        extra_fields["Number of reaches"] = geom.n_reaches
        extra_fields["Number of cross sections"] = geom.n_cross_sections
        extra_fields["Number of junctions"] = geom.n_junctions

    elif re.match(".p[0-9]{2}", file_extension):
        roles.extend(["plan", "hec-ras", pystac.MediaType.TEXT])
        description = """Plan file for ras."""
        if bucket:
            string = str_from_s3(asset_key, client, bucket)
            plan = RasPlanText.from_str(string, rms.crs, " .p01")
        else:
            plan = [i for i in rm.plans.values() if i.file_extension == file_extension][0]
        extra_fields["Title"] = plan.title
        # extra_fields["Geometry Title"] = plan.geom.title
        # extra_fields["Geometry Extension"] = plan.geom.file_extension
        # extra_fields["Flow Title"] = plan.flow.title
        # extra_fields["Flow Extension"] = plan.flow.file_extension

    elif re.match(".O[0-9]{2}", file_extension):
        roles.extend(["output", "hec-ras", pystac.MediaType.TEXT])
        description = """Output file for ras."""

    elif re.match(".r[0-9]{2}", file_extension):
        roles.extend(["run", "hec-ras", pystac.MediaType.TEXT])
        description = """Run file for ras."""

    elif re.match(".rasmap", file_extension):
        roles.extend(["rasmap", "hec-ras", pystac.MediaType.XML])
        description = """Rasmapper file for ras."""

    elif ".rasmap.backup" in title:
        roles.extend(["rasmap-backup", "hec-ras", pystac.MediaType.XML])
        description = """Rasmapper backup file for ras."""

    elif ".computeMsgs.txt" in title:
        roles.extend(["compute-messages", "hec-ras", pystac.MediaType.TEXT])
        description = """Compute messages file for ras."""

    elif file_extension == ".png":
        roles.extend(["thumbnail", pystac.MediaType.PNG])
        description = """PNG of geometry with OpenStreetMap basemap."""
        title = "Thumbnail"

    elif re.match(".gpkg", file_extension):
        roles.extend(["ras-geometry-gpkg", pystac.MediaType.GEOPACKAGE])
        description = """GeoPackage file with geometry data extracted from .gxx file."""
        title = title

    elif ".ripple1d.json" in title:
        roles.extend(["ripple-parameters", pystac.MediaType.JSON])
        description = """Json file containing Ripple parameters."""
        title = "Ripple parameters"

    elif ".stac.json" in title:
        roles.extend(["stac-item", pystac.MediaType.JSON])
        description = """Json for this stac-item."""
        title = title

    elif file_extension == ".prj":
        if bucket:
            _, client, _ = init_s3_resources()
            string = str_from_s3(asset_key, client, bucket)
        else:
            with open(asset_key, "r") as file:
                string = file.read()
        if "Proj Title=" in string:
            roles.extend(["project-file", "hec-ras", pystac.MediaType.TEXT])
            description = """Project file for ras."""
        else:
            if isinstance(rms, NwmReachModel):
                if asset_key in rms.terrain_assets:
                    roles.extend(["Terrain", "projection-file", pystac.MediaType.TEXT])
                else:
                    roles.extend(["projection-file", pystac.MediaType.TEXT])
                description = """Projection file for ras."""

    if isinstance(rms, NwmReachModel):
        if file_extension == ".hdf":
            if asset_key in rms.terrain_assets:
                roles.extend(["Terrain", pystac.MediaType.HDF])
            else:
                roles.extend(["hec-ras", pystac.MediaType.HDF])

        elif file_extension == ".vrt":
            if asset_key in rms.terrain_assets:
                roles.extend(["Terrain", pystac.MediaType.XML])

        elif file_extension == ".tif":
            if asset_key in rms.terrain_assets:
                roles.extend(["Terrain", pystac.MediaType.GEOTIFF])

    return {"roles": roles, "description": description, "title": title, "extra_fields": extra_fields}


def find_hash(item_metadata: Dict, asset_file: str) -> dict:
    """
    Extract the hash value for a given asset file from a metadata dictionary.

    This function searches through a metadata dictionary for an asset file based on the file's extension.
    It then extracts and returns the hash value associated with that file extension from the metadata.

    Parameters
    ----------
        item_metadata (dict): A dictionary containing metadata items with hash info.
        asset_file (str): The path to the asset file.

    Returns
    -------
        hash_dict (Dict): A dictionary with a single key-value pair where the value is the hash for the
        asset file. Returns an empty dictionary if no hash is found for the file extension.
    """
    hash_dict = {}

    file_extension = Path(asset_file).suffix.lstrip(".")
    for key in item_metadata.keys():
        key_file_ext = key.split(":")[-1]
        if file_extension == key_file_ext:
            hash_dict = {"hash": item_metadata[key]["Hash"]}

    return hash_dict


def remove_hash_from_metadata(item_metadata: Dict) -> dict:
    """
    Remove "Hash" from each key (if it exists) in metedata dictionary.

    Parameters
    ----------
        item_metadata (Dict): Dictionary of item metadata with hashs.

    Returns
    -------
        no_hash_metadata (Dict): New metadata dictionary without hash info.
    """
    # Copy the dictionary to avoid modifying the original
    no_hash_metadata = item_metadata.copy()
    for key in no_hash_metadata:
        if "Hash" in no_hash_metadata[key]:
            del no_hash_metadata[key]["Hash"]
    return no_hash_metadata
