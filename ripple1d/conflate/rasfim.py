"""Conflation classes and functions."""

import logging
import os
import sqlite3
import traceback
from collections import OrderedDict
from typing import List, Optional, Tuple

import fiona
import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
from fiona.errors import DriverError
from shapely import LineString, MultiLineString, Point, Polygon, box, reverse
from shapely.ops import linemerge, nearest_points, transform

from ripple1d.consts import (
    DEFAULT_ND_SLOPE,
    MAX_ND_SLOPE,
    METERS_PER_FOOT,
    MIN_ND_SLOPE,
    ND_SLOPE_SIG_FIGS,
)
from ripple1d.errors import BadConflation
from ripple1d.utils.ripple_utils import (
    NWMWalker,
    RASWalker,
    check_xs_direction,
    clip_ras_centerline,
    fix_reversed_xs,
    validate_point,
    xs_concave_hull,
)

NWM_CRS = """PROJCRS["USA_Contiguous_Albers_Equal_Area_Conic_USGS_version",
    BASEGEOGCRS["NAD83",DATUM["North American Datum 1983",
    ELLIPSOID["GRS 1980",6378137,298.257222101004,LENGTHUNIT["metre",1]]],
    PRIMEM["Greenwich",0,ANGLEUNIT["degree",0.0174532925199433]],
    ID["EPSG",4269]],CONVERSION["unnamed",METHOD["Albers Equal Area",
    ID["EPSG",9822]],PARAMETER["Latitude of 1st standard parallel"
    ,29.5,ANGLEUNIT["degree",0.0174532925199433],ID["EPSG",8823]]
    ,PARAMETER["Latitude of 2nd standard parallel",45.5,
    ANGLEUNIT["degree",0.0174532925199433],ID["EPSG",8824]],
    PARAMETER["Latitude of false origin",23,ANGLEUNIT["degree",0.0174532925199433],
    ID["EPSG",8821]],PARAMETER["Longitude of false origin",-96,
    ANGLEUNIT["degree",0.0174532925199433],ID["EPSG",8822]],
    PARAMETER["Easting at false origin",0,LENGTHUNIT["metre",1],
    ID["EPSG",8826]],PARAMETER["Northing at false origin",0,
    LENGTHUNIT["metre",1],ID["EPSG",8827]]],CS[Cartesian,2],
    AXIS["easting",east,ORDER[1],LENGTHUNIT["metre",1,ID["EPSG",9001]]],
    AXIS["northing",north,ORDER[2],LENGTHUNIT["metre",1,ID["EPSG",9001]]]]"""


def ensure_geometry_column(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Confirm that there exists a geometry column in the GeoDataFrame."""
    if "geom" in gdf.columns:
        return gdf
    elif "geometry" in gdf.columns:
        gdf.rename(columns={"geometry": "geom"}, inplace=True)
        gdf.set_geometry("geom")
        return gdf
    else:
        raise KeyError(f"Expecting a `geom` or `geometry` column, did not find in {gdf.columns}")


class RasFimConflater:
    """
    Conflate NWM and RAS data for a single river reach.

    Args:
        nwm_pq (str): Path to the NWM Parquet file converted to parquet from:
            s3://noaa-nws-owp-fim/rasfim/inputs/X-National_Datasets/nwm_flows.gpkg
        source_model_directory (str): Path to the Source HEC-RAS model directory.
        ras_model_name (str): Name of the HEC-RAS model.
        load_data (bool, optional): Load the data on initialization. Defaults to True.

    Raises
    ------
        ValueError: Required layer not found in the GeoPackage
        DriverError: Unable to read the GeoPackage
    """

    def __init__(
        self,
        nwm_pq: str,
        source_model_directory: str,
        ras_model_name: str,
        output_concave_hull_path: str = None,
        min_flow_multiplier: float = 0.9,
        max_flow_multiplier: float = 1.2,
    ):
        self.nwm_pq = nwm_pq
        self.source_model_directory = source_model_directory
        self.ras_model_name = ras_model_name
        self.ras_gpkg = os.path.join(source_model_directory, f"{self.ras_model_name}.gpkg")

        self.output_concave_hull_path = output_concave_hull_path
        self.nwm_reaches = None

        self.min_flow_multiplier = min_flow_multiplier
        self.max_flow_multiplier = max_flow_multiplier

        self._ras_centerlines = None
        self._ras_xs = None
        self._ras_structures = None
        self._ras_junctions = None
        self._ras_metadata = None
        self._common_crs = None
        self._xs_hulls = None
        self._common_crs = NWM_CRS
        self.load_data()
        self.nwm_walker = NWMWalker(None, network_df=self.nwm_reaches)
        self.ras_walker = RASWalker(self.ras_gpkg)

    def __repr__(self):
        """Return the string representation of the object."""
        return f"RasFimConflater(nwm_pq={self.nwm_pq}, ras_gpkg={self.ras_gpkg})"

    @property
    def stac_api(self):
        """The stac_api for the HEC-RAS Model."""
        if self.ras_metadata:
            if "stac_api" in self.ras_metadata.keys():
                return self.ras_metadata.get("stac_api")

    @property
    def stac_collection_id(self):
        """The stac_collection_id for the HEC-RAS Model."""
        if self.ras_metadata:
            if "stac_collection_id" in self.ras_metadata.keys():
                return self.ras_metadata.get("stac_collection_id")

    @property
    def stac_item_id(self):
        """The stac_item_id for the HEC-RAS Model."""
        if self.ras_metadata:
            if "stac_item_id" in self.ras_metadata.keys():
                return self.ras_metadata.get("stac_item_id")

    @property
    def primary_geom_file(self):
        """The primary geometry file for the HEC-RAS Model."""
        if self.ras_metadata:
            return self.ras_metadata.get("primary_geom_file")

    @property
    def primary_flow_file(self):
        """The primary flow file for the HEC-RAS Model."""
        if self.ras_metadata:
            return self.ras_metadata.get("primary_flow_file")

    @property
    def primary_plan_file(self):
        """The primary plan file for the HEC-RAS Model."""
        if self.ras_metadata:
            return self.ras_metadata.get("primary_plan_file")

    @property
    def ras_project_file(self):
        """The source HEC-RAS project file."""
        if self.ras_metadata:
            return self.ras_metadata.get("ras_project_file")

    @property
    def units(self):
        """Units of the source HEC-RAS model."""
        if self.ras_metadata is not None:
            return self.ras_metadata.get("units")

    def populate_r_station(self, row: pd.Series, assume_ft: bool = True) -> str:
        """Populate the r value for a cross section. The r value is the ratio of the station to actual cross section length."""
        # TODO check if this is the correct way to calculate r
        df = pd.DataFrame(row["station_elevation_points"], index=["elevation"]).T
        return df.index.max() / (row.geometry.length / METERS_PER_FOOT)

    @property
    def _gpkg_metadata(self):
        """Metadata from gpkg."""
        with sqlite3.connect(self.ras_gpkg) as conn:
            cur = conn.cursor()
            cur.execute("select * from metadata")
            return dict(cur.fetchall())

    def determine_station_order(self, xs_gdf: gpd.GeoDataFrame, reach: LineString):
        """Determine the order based on stationing of the cross sections along the reach."""
        rs = []
        for _, xs in xs_gdf.iterrows():
            geom = reach.intersection(xs.geometry)
            try:
                point = validate_point(geom)
                rs.append(reach.project(point))
            except TypeError:
                rs.append(rs[-1])

        xs_gdf["rs"] = rs
        return xs_gdf.sort_values(by="rs", ignore_index=True)

    def add_hull(self, xs_gdf: gpd.GeoDataFrame, reach: LineString):
        """Add the concave hull to the GeoDataFrame."""
        if len(xs_gdf) > 1:
            xs_gdf = self.determine_station_order(xs_gdf, reach)
            hull = xs_concave_hull(xs_gdf)
            if self._xs_hulls is None:
                self._xs_hulls = hull
            else:
                self._xs_hulls = pd.concat([self._xs_hulls, hull])

    def write_hulls(self):
        """Write the hulls to a GeoPackage."""
        if self._xs_hulls is not None:
            self._xs_hulls.to_file(self.output_concave_hull_path, driver="GPKG", layer="xs_concave_hulls")

    def set_ras_gpkg(self, ras_gpkg, load_data=True):
        """Set the RAS GeoPackage and optionally load the data."""
        self.ras_gpkg = ras_gpkg
        if load_data:
            self.load_gpkg(self.ras_gpkg)

    def load_gpkg(self, gpkg: str):
        """Load the RAS data from the GeoPackage."""
        layers = fiona.listlayers(gpkg)
        if "River" in layers:
            self._ras_centerlines = gpd.read_file(self.ras_gpkg, layer="River")
        if "XS" in layers:
            xs = gpd.read_file(self.ras_gpkg, layer="XS")
            self._ras_xs = xs  # xs[xs.intersects(self._ras_centerlines.union_all())]
        if "Junction" in layers:
            self._ras_junctions = gpd.read_file(self.ras_gpkg, layer="Junction")
        if "Structure" in layers:
            structures = gpd.read_file(self.ras_gpkg, layer="Structure")
            self._ras_structures = structures[structures.intersects(self._ras_centerlines.union_all())]
        if "metadata" in layers:
            self._ras_metadata = self._gpkg_metadata

    def load_pq(self, nwm_pq: str):
        """Load the NWM data from the Parquet file."""
        try:
            # read nwm reaches with bbox
            nwm_reaches = gpd.read_parquet(nwm_pq, bbox=self._ras_xs.to_crs(self.common_crs).total_bounds)

            # rename geometry
            nwm_reaches = nwm_reaches.rename(columns={"geom": "geometry"})
            self.nwm_reaches = nwm_reaches.set_geometry("geometry")

        except Exception as e:
            if type(e) == DriverError:
                raise DriverError(f"Unable to read {nwm_pq}")
            else:
                raise (e)

    def load_data(self):
        """Load the NWM and RAS data from the GeoPackages."""
        self.load_gpkg(self.ras_gpkg)
        self.load_pq(self.nwm_pq)

    @property
    def common_crs(self):
        """Return the common CRS for the NWM and RAS data."""
        return self._common_crs

    @property
    def ras_centerlines(self) -> gpd.GeoDataFrame:
        """RAS centerlines."""
        return self._ras_centerlines.to_crs(self.common_crs)

    @property
    def ras_river_reach_names(self) -> List[str]:
        """Return the unique river reach names in the RAS data."""
        return self.ras_centerlines["river_reach"].unique().tolist()

    @property
    def ras_xs(self) -> gpd.GeoDataFrame:
        """RAS cross sections."""
        return self._ras_xs.to_crs(self.common_crs)

    @property
    def ras_structures(self) -> gpd.GeoDataFrame:
        """RAS structures."""
        # logging.info("RAS structures")
        try:
            return self._ras_structures.to_crs(self.common_crs)
        except AttributeError:
            return None

    @property
    def ras_junctions(self) -> gpd.GeoDataFrame:
        """RAS junctions."""
        try:
            return self._ras_junctions.to_crs(self.common_crs)
        except AttributeError:
            return None

    @property
    def ras_metadata(self) -> Polygon:
        """RAS metadata."""
        try:
            return self._ras_metadata
        except AttributeError:
            return None

    @property
    def ras_xs_bbox(self) -> Polygon:
        """Return the bounding box for the RAS cross sections."""
        return self.get_projected_bbox(self.ras_xs)

    def ras_xs_concave_hull(self, river_reach_name: str = None) -> Polygon:
        """Return the concave hull of the cross sections."""
        if river_reach_name is None:
            return xs_concave_hull(fix_reversed_xs(self.ras_xs, self.ras_centerlines), self.ras_junctions)
        else:
            xs = self.ras_xs[self.ras_xs["river_reach"] == river_reach_name]
            return xs_concave_hull(
                fix_reversed_xs(
                    self.ras_xs, self.ras_centerlines.loc[self.ras_centerlines["river_reach"] == river_reach_name]
                )
            )

    def ras_xs_convex_hull(self, river_reach_name: str = None):
        """Return the convex hull of the cross sections."""
        lines = pd.concat([self.ras_xs, self.ras_centerlines])
        if river_reach_name:
            polygon = lines[lines["river_reach"] == river_reach_name]["geometry"].union_all().convex_hull

        else:
            polygon = lines["geometry"].union_all().convex_hull

        return gpd.GeoDataFrame({"geometry": [polygon]}, geometry="geometry", crs=self.ras_xs.crs)

    @property
    def ras_banks(self):
        """Return the banks of the RAS cross sections."""
        raise NotImplementedError

    def get_projected_bbox(self, gdf: gpd.GeoDataFrame) -> Polygon:
        """Return the bounding box for the GeoDataFrame in the common CRS."""
        assert gdf.crs, "GeoDataFrame must have a CRS"
        project = pyproj.Transformer.from_crs(gdf.crs, NWM_CRS, always_xy=True).transform
        return transform(project, box(*tuple(gdf.total_bounds)))

    def local_nwm_reaches(self, river_reach_name: str = None, buffer=0) -> gpd.GeoDataFrame:
        """NWM reaches that intersect the RAS cross sections."""
        if river_reach_name:

            return self.nwm_reaches[
                self.nwm_reaches.intersects(
                    self.ras_xs_convex_hull(river_reach_name)["geometry"].iloc[0].buffer(buffer)
                )
            ]
        else:
            return self.nwm_reaches[
                self.nwm_reaches.intersects(self.ras_xs_convex_hull()["geometry"].iloc[0].buffer(buffer))
            ]

    @property
    def local_gages(self) -> dict:
        """Local gages for the NWM reaches."""
        local_reaches = self.local_nwm_reaches()
        gages = local_reaches["gages"]
        reach_ids = local_reaches["ID"]
        return {reach_id: gage for reach_id, gage in zip(reach_ids, gages) if pd.notna(gage) and gage.strip()}

    def local_lakes(self):
        """Local lakes for the NWM reaches."""
        raise NotImplementedError

    def check_centerline(func):
        """
        Ensure that ras_centerline is available and that the centerline is specified.

        Helper function.

        If the centerline is not specified, the first centerline is used, with a check that there is only one centerline.
        """

        def wrapper(self, *args, **kwargs):

            river_reach_name = kwargs.get("river_reach_name", None)

            if river_reach_name:
                logging.debug(f"check_centerline river_reach_name: {river_reach_name}")
                centerline = self.ras_centerline_by_river_reach_name(river_reach_name)
            else:
                if self.ras_centerlines.shape[0] == 1:
                    centerline = self.ras_centerlines.geometry.iloc[0]
                else:
                    raise ValueError("Multiple centerlines found, please specify river_reach_name")
            kwargs["centerline"] = centerline
            return func(self, *args, **kwargs)

        return wrapper

    @check_centerline
    def ras_start_end_points(
        self, river_reach_name: str = None, centerline=None, clip_to_xs=False, buffer=0
    ) -> Tuple[Point, Point]:
        """
        Get the start and end points for a RAS centerline.

        If clip_to_xs is True try to clip the centerline to the most upstream and downstream cross sections.
        If this fails then return the original centerline.
        """
        if river_reach_name:
            centerline = self.ras_centerline_by_river_reach_name(river_reach_name)
            try:
                if clip_to_xs:
                    centerline = clip_ras_centerline(centerline, self.xs_by_river_reach_name(river_reach_name), buffer)
                return endpoints_from_multiline(centerline)
            except Exception:
                return endpoints_from_multiline(centerline)

    def ras_centerline_by_river_reach_name(self, river_reach_name: str) -> LineString:
        """Return the centerline for the specified river reach."""
        return self.ras_centerlines[self.ras_centerlines["river_reach"] == river_reach_name].geometry.iloc[0]

    def xs_by_river_reach_name(self, river_reach_name: str) -> gpd.GeoDataFrame:
        """Return the cross sections for the specified river reach."""
        return self.ras_xs[self.ras_xs["river_reach"] == river_reach_name]

    def get_nwm_reach_metadata(self, reach_id) -> dict:
        """Make a dictionary with relevant NWM information."""
        metadata = {}
        flow_data = self.nwm_reaches[self.nwm_reaches["ID"] == reach_id].iloc[0]
        if isinstance(flow_data["high_flow_threshold"], float):
            metadata["low_flow"] = int(flow_data["high_flow_threshold"] * self.min_flow_multiplier)
        else:
            metadata["low_flow"] = -9999
            logging.warning(f"No low flow data for {reach_id}")

        try:
            high_flow = float(flow_data["f100year"]) * self.max_flow_multiplier
            metadata["high_flow"] = int(high_flow)
        except:
            logging.warning(f"No high flow data for {reach_id}")
            metadata["high_flow"] = -9999

        inverted = metadata["high_flow"] < metadata["low_flow"]
        negative = metadata["low_flow"] < 0 or metadata["high_flow"] < 0
        if negative or inverted:
            raise RuntimeError(f'Invalid flow combination. Low: {metadata["low_flow"]}. High: {metadata["high_flow"]}')

        try:
            metadata["network_to_id"] = str(flow_data["to_id"])
        except:
            logging.warning(f"No to_id data for {reach_id}")
            metadata["network_to_id"] = "-9999"

        if reach_id in self.local_gages.keys():
            gage_id = self.local_gages[reach_id].replace(" ", "")
            metadata["gage"] = gage_id
            metadata["gage_url"] = f"https://waterdata.usgs.gov/nwis/uv?site_no={gage_id}&legacy=1"

        return metadata


# general geospatial functions
def endpoints_from_multiline(mline: MultiLineString) -> Tuple[Point, Point]:
    """Return the start and end points of a MultiLineString."""
    if isinstance(mline, MultiLineString):
        # TODO: Ensure the centerlines in the GPKG are always the same type (i.e. MultiLineString)
        merged_line = linemerge(mline)
    else:
        merged_line = mline
    if isinstance(merged_line, MultiLineString):  # if line is still multilinestring raise error
        raise TypeError(f"Could not convert {type(merged_line)} to LineString")

    start_point, end_point = merged_line.coords[0], merged_line.coords[-1]
    return Point(start_point), Point(end_point)


def nearest_line_to_point(
    lines: gpd.GeoDataFrame, point: Point, column_id: str = "ID", search_radius: int = 1e9, number_of_returns: int = 0
) -> np.array:
    """
    Return the ID of the line(s) closest to the point.

    The number of lines to return is specified with the 'number_of_returns' arg.
    The 'search_radius' is a max distance that a line can be away from the point to be returned.
    """
    lines["distance"] = lines.distance(point)  # compute distance to point for all lines
    subset = lines.iloc[lines["distance"].argsort().values].iloc[
        : number_of_returns + 1
    ]  # grab closest x lines (number_of_returns)
    return subset.loc[
        subset["distance"] <= search_radius, column_id
    ].values  # ensure the return lines are within the search radius


def convert_linestring_to_points(
    linestring: LineString, crs: pyproj.crs.crs.CRS, point_spacing: int = 5
) -> gpd.GeoDataFrame:
    """Convert a LineString to a GeoDataFrame of Points."""
    num_points = int(round(linestring.length / point_spacing))

    if num_points == 0:
        num_points = 1

    points = [linestring.interpolate(distance) for distance in np.linspace(0, linestring.length, num_points)]

    return gpd.GeoDataFrame(
        geometry=points,
        crs=crs,
    )


def cacl_avg_nearest_points(reference_gdf: gpd.GeoDataFrame, compare_points_gdf: gpd.GeoDataFrame) -> float:
    """Calculate the average distance between the reference points and the nearest points in the comparison GeoDataFrame."""
    multipoint = compare_points_gdf.geometry.union_all()
    reference_gdf["nearest_distance"] = reference_gdf.geometry.apply(
        lambda point: point.distance(nearest_points(point, multipoint)[1])
    )
    return reference_gdf["nearest_distance"].mean()


def count_intersecting_lines(ras_xs: gpd.GeoDataFrame, nwm_reaches: gpd.GeoDataFrame) -> int:
    """Return the number of intersecting lines between the RAS cross sections and the NWM reaches."""
    if ras_xs.crs != nwm_reaches.crs:
        ras_xs = ras_xs.to_crs(nwm_reaches.crs)
    join_gdf = gpd.sjoin(ras_xs, nwm_reaches, predicate="intersects")
    return join_gdf


# analytical functions
def walk_network(gdf: gpd.GeoDataFrame, start_id: int, stop_id: int, river_reach_name: str) -> List[int]:
    """Walk the network from the start ID to the stop ID."""
    current_id = start_id
    ids = [current_id]

    while current_id != stop_id:
        result = gdf.query(f"ID == {current_id}")

        if result.empty:
            logging.error(
                f"No row found with ID = {current_id} | start_id: {start_id} | stop_id: {stop_id} | RAS river-reach: {river_reach_name}"
            )
            break

        to_value = result.iloc[0]["to_id"]
        ids.append(int(to_value))
        current_id = to_value
        if current_id == stop_id:
            break

    return ids


def ras_xs_geometry_data(rfc: RasFimConflater, xs_id: str) -> dict:
    """Return the geometry data (max/min xs elevation) for the specified cross section."""
    # TODO: Need to verify units in the RAS data
    xs = rfc.ras_xs[rfc.ras_xs["river_reach_rs"] == xs_id]
    if xs.shape[0] > 1:
        raise ValueError(f"Multiple XS found with ID = {xs_id}")

    elif xs.shape[0] == 0:
        raise ValueError(f"No XS found with ID = {xs_id}")
    else:
        xs = xs.iloc[0]

    xs.rename({"thalweg": "min_elevation", "xs_max_elevation": "max_elevation", "river_station": "xs_id"}, inplace=True)
    xs_data = xs[["river", "reach", "min_elevation", "max_elevation", "xs_id"]].to_dict()

    return xs[["river", "reach", "min_elevation", "max_elevation", "xs_id"]].to_dict()


def get_us_most_xs_from_junction(rfc, us_river, us_reach):
    """Search for river reaches at junctions and return xs closest ds reach at junction."""
    if rfc.ras_junctions is None:
        raise ValueError("No junctions found in the HEC-RAS model.")
    ds_rivers, ds_reaches = None, None
    for row in rfc.ras_junctions.itertuples():
        us_rivers = row.us_rivers.split(",")
        us_reaches = row.us_reaches.split(",")
        for river, reach in zip(us_rivers, us_reaches):

            if river == us_river and reach == us_reach:
                ds_rivers = row.ds_rivers.split(",")
                ds_reaches = row.ds_reaches.split(",")
                break

    if not ds_rivers:
        raise ValueError(f"No downstream rivers found for {us_river}")
    elif len(ds_rivers) == 0:
        raise ValueError(f"No downstream rivers found for {us_river}")
    elif len(ds_rivers) > 1:
        raise NotImplementedError(f"Multiple downstream rivers found for {us_river}")
    else:
        ds_river = ds_rivers[0]

    if not ds_reaches:
        raise ValueError(f"No downstream reaches found for {us_reach}")
    elif len(ds_reaches) == 0:
        raise ValueError(f"No downstream reaches found for {us_reach}")
    elif len(ds_reaches) > 1:
        raise NotImplementedError(f"Multiple downstream reaches found for {us_reach}")
    else:
        ds_reach = ds_reaches[0]

    ds_river_reach_gdf = rfc.ras_xs.loc[(rfc.ras_xs["river"] == ds_river) & (rfc.ras_xs["reach"] == ds_reach), :]
    max_rs = ds_river_reach_gdf.loc[:, "river_station"].max()
    ds_xs_id = ds_river_reach_gdf.loc[ds_river_reach_gdf["river_station"] == max_rs, "river_reach_rs"].iloc[0]
    return ds_xs_id


def check_for_us_xs(river_reach_rs, xs_gdf: gpd.GeoDataFrame) -> str:
    """
    Check for another cross section upstream of the most upstream intersected cross section.

    Retrieve the river_reach_rs id for the cross section upstream of the most upstream intersected
    cross section. If there are no cross sections upstream of the
    most upstream intersecting cross section, then return the most upstream intersecting cross section.
    """
    # get cross section using river_reach_rs
    xs = xs_gdf.loc[xs_gdf["river_reach_rs"] == river_reach_rs].iloc[0]

    # get river station of the us cross section
    us_rs = xs_gdf.loc[
        (xs_gdf["river_reach"] == xs["river_reach"]) & (xs_gdf["river_station"] > xs["river_station"]), "river_station"
    ].min()

    # return the river_reach_rs
    if np.isnan(us_rs):
        return river_reach_rs
    elif isinstance(float(us_rs), float):
        return xs_gdf.loc[
            (xs_gdf["river_station"] == us_rs) & (xs_gdf["river_reach"] == xs["river_reach"]), "river_reach_rs"
        ].iloc[0]

    else:
        return river_reach_rs


def map_reach_xs(rfc: RasFimConflater, reach: gpd.GeoSeries) -> dict:
    """
    Map the upstream and downstream cross sections for the nwm reach.

    TODO: This function needs helper functions to extract the datasets from the GeoDataFrames.
    """
    # get the xs that intersect the nwm reach
    intersected_xs = rfc.ras_xs[rfc.ras_xs.intersects(reach.geometry)]
    if intersected_xs.empty:
        return

    not_reversed_xs = check_xs_direction(intersected_xs, reach.geometry)
    intersected_xs["geometry"] = intersected_xs.apply(
        lambda row: (
            row.geometry if row["river_reach_rs"] in list(not_reversed_xs["river_reach_rs"]) else reverse(row.geometry)
        ),
        axis=1,
    )

    # find most us and ds xs making sure that they are hydrologicaly connected.
    us_xs, ds_xs = retrieve_us_ds_xs(rfc, intersected_xs, reach)

    # determine if us end of the nwm reach is between two cross sections. If so grab the upstream cross section (only if stream order=1)
    if reach.stream_order == 1 and reach.ID not in rfc.nwm_reaches["to_id"].values:
        us_xs = check_for_us_xs(us_xs, rfc.ras_xs)

    # Initialize us_xs data with min /max elevation, then build the dict with added info
    us_data = ras_xs_geometry_data(rfc, us_xs)

    # get info for the current ds_xs
    ras_river = rfc.ras_xs.loc[rfc.ras_xs["river_reach_rs"] == ds_xs, "river"].iloc[0]
    ras_reach = rfc.ras_xs.loc[rfc.ras_xs["river_reach_rs"] == ds_xs, "reach"].iloc[0]
    river_station = rfc.ras_xs.loc[rfc.ras_xs["river_reach_rs"] == ds_xs, "river_station"].iloc[0]
    river_reach_gdf = rfc.ras_xs.loc[(rfc.ras_xs["river"] == ras_river) & (rfc.ras_xs["reach"] == ras_reach), :]

    # check if this is the most downstream xs on this ras reach
    if river_reach_gdf["river_station"].min() == river_station:
        try:
            ds_xs = get_us_most_xs_from_junction(rfc, ras_river, ras_reach)
            ds_data = ras_xs_geometry_data(rfc, ds_xs)
            logging.debug(
                f"Conflating to junction for reach {reach.ID}: {us_data['river']} {us_data['reach']} to {ds_data['river']} {ds_data['reach']}"
            )
        except ValueError as e:
            ds_data = ras_xs_geometry_data(rfc, ds_xs)
            logging.debug(f"No downstream XS's: {e}")

    # if it is not the most downstream xs on this ras reach then compute the next downstream river station and determine the id
    else:
        ds_xs_gdf = river_reach_gdf.loc[river_reach_gdf["river_station"] < river_station].reset_index(drop=True)
        if len(ds_xs_gdf) == 1:
            index = 0
        else:
            index = ds_xs_gdf["river_station"].idxmax()
        ds_xs = ds_xs_gdf["river_reach_rs"].iloc[index]
        ds_data = ras_xs_geometry_data(rfc, ds_xs)

    # add xs concave hull
    if rfc.output_concave_hull_path:
        xs_gdf = pd.concat([intersected_xs, rfc.ras_xs[rfc.ras_xs["river_reach_rs"] == ds_xs]], ignore_index=True)
        rfc.add_hull(xs_gdf, reach.geometry)

    if us_data == ds_data:
        return

    return {"us_xs": us_data, "ds_xs": ds_data, "eclipsed": False}


def calculate_reach_coverage(xs: pd.DataFrame, reach: gpd.GeoDataFrame) -> pd.DataFrame:
    """Aggregate intersected sections by reach, calculate coverage, and remove backwards reaches."""
    # Calculate distances along NWM line
    xs["intersecting_point"] = xs.apply(lambda x: x.geometry.intersection(reach.geometry), axis=1)
    xs["nwm_rs"] = xs.apply(
        lambda x: (
            reach.geometry.project(x.intersecting_point)
            if isinstance(x.intersecting_point, Point)
            else reach.geometry.project(x.intersecting_point.geoms[0])
        ),
        axis=1,
    )

    # Calculate coverage
    reach_coverage = {}
    for reach in xs["river_reach"].unique():
        # Subset to reach of interest
        subset = xs[xs["river_reach"] == reach]

        # Calculate coverage
        max_rs = subset["nwm_rs"].max()
        min_rs = subset["nwm_rs"].min()
        coverage = max_rs - min_rs

        # Check if reaches are oriented opposite directions
        ds_ras_rs = subset[subset["nwm_rs"] == max_rs]["river_station"].iloc[0]
        us_ras_rs = subset[subset["nwm_rs"] == min_rs]["river_station"].iloc[0]
        if us_ras_rs >= ds_ras_rs:
            reach_coverage[reach] = {"coverage": coverage, "min_rs": min_rs}

    if len(reach_coverage) == 0:
        raise BadConflation(f"Reach {reach} has no cross-sections that overlap in the correct direction.")
    df = pd.DataFrame.from_dict(reach_coverage, orient="index")
    return df.sort_values(by="min_rs")  # note nwm_rs is from us to ds; i.e., ds_rs>us_rs


def retrieve_us_ds_xs(rfc: RasFimConflater, intersected_xs: gpd.GeoDataFrame, reach: gpd.GeoSeries) -> (str, str):
    """Check that us and ds are hydrologically connected. Select reach with most overlap if not."""
    if len(intersected_xs["river_reach"].unique()) == 1:
        intersected_xs = intersected_xs.sort_values(by="river_station")
        return intersected_xs["river_reach_rs"].iloc[-1], intersected_xs["river_reach_rs"].iloc[0]

    # Calculate coverage
    reach_coverage = calculate_reach_coverage(intersected_xs, reach)

    linked_reaches = []
    for _, row in reach_coverage.iterrows():  # start with the upstream most river station

        if row["min_rs"] == reach_coverage["min_rs"].min():  # if this is the us most reach append to linked_reaches
            linked_reaches.append([row.name])
            previous_reach = row.name
            continue
        new_reach = None
        for i, linked_reach in enumerate(linked_reaches):  # check if connected to previous (upstream) linked_reaches
            if rfc.ras_walker.are_connected(linked_reach[-1], row.name):
                new_reach = row.name
                break
        if new_reach is not None:
            linked_reaches[i].append(row.name)
        else:
            linked_reaches.append(
                [row.name]
            )  # if not connected to previous linked reaches add a new set of linked reaches to the list of linked reaches
        previous_reach = row.name

    coverage = [
        reach_coverage.loc[reach_coverage.index.isin(linked_reach), ["coverage"]].sum().iloc[0]
        for linked_reach in linked_reaches
    ]

    selected_linked_reach = linked_reaches[coverage.index(max(coverage))]
    us_reach = intersected_xs.loc[intersected_xs["river_reach"] == selected_linked_reach[0]]
    ds_reach = intersected_xs.loc[intersected_xs["river_reach"] == selected_linked_reach[-1]]

    us_xs = us_reach.loc[us_reach["river_station"] == us_reach["river_station"].max(), "river_reach_rs"].iloc[0]
    ds_xs = ds_reach.loc[ds_reach["river_station"] == ds_reach["river_station"].min(), "river_reach_rs"].iloc[0]

    return us_xs, ds_xs


def _ds_xs_geometry(rfc: RasFimConflater, ras_xs_data: dict) -> LineString:
    """Return the geometry for the finalized downstream RAS cross section."""
    ds_xs = ras_xs_data["ds_xs"]
    ras_xs = rfc.ras_xs
    matches = ras_xs[
        (ras_xs["river"] == ds_xs["river"])
        & (ras_xs["reach"] == ds_xs["reach"])
        & (ras_xs["river_station"] == ds_xs["xs_id"])
    ]
    if matches.empty:
        raise KeyError(f"Could not find downstream XS geometry for {ds_xs}")
    return matches.geometry.iloc[0]


def _clamp_nd_slope(value: float) -> float:
    """Clamp a normal-depth slope to [MIN_ND_SLOPE, MAX_ND_SLOPE].

    Logs a WARNING when the value is outside the bounds.
    """
    clamped = min(max(value, MIN_ND_SLOPE), MAX_ND_SLOPE)
    if clamped != value:
        logging.warning(
            "Clamped ds_slope from %s to %s (bounds [%s, %s])",
            value, clamped, MIN_ND_SLOPE, MAX_ND_SLOPE,
        )
    return clamped


def _select_ds_slope_reach(
    candidates: gpd.GeoDataFrame, current_reach_id, tree_dict: dict
) -> Optional[pd.Series]:
    """Select the NWM reach whose slope should define the downstream boundary.

    When the downstream XS intersects multiple NWM reaches, walk downstream
    from the current reach via the to_id chain and pick the furthest
    downstream candidate. The current reach is a valid candidate if it
    intersects the ds_xs.
    """
    if len(candidates) == 1:
        return candidates.iloc[0]

    candidate_ids = set(candidates["ID"])
    most_downstream = current_reach_id if current_reach_id in candidate_ids else None
    visited = {current_reach_id}
    current = current_reach_id

    while current in tree_dict:
        current = tree_dict[current]
        if current in visited:
            break
        visited.add(current)
        if current in candidate_ids:
            most_downstream = current  # last match in walk = furthest downstream

    if most_downstream is None:
        return None
    return candidates[candidates["ID"] == most_downstream].iloc[0]


def get_ds_boundary_slope(
    rfc: RasFimConflater, reach_id, ras_xs_data: dict
) -> Tuple[float, Optional[str]]:
    """Return the normal-depth slope from the NWM reach intersecting the downstream XS.

    Falls back to the current reach's slope, then DEFAULT_ND_SLOPE, when no
    candidate is found on the downstream path. Returned slope is the raw
    hydrofabric value; callers persisting it for the 1D HEC-RAS steady-flow
    solver must clamp it via `_clamp_nd_slope`.
    """
    current_matches = rfc.nwm_reaches[rfc.nwm_reaches["ID"] == reach_id]
    current_reach_row = current_matches.iloc[0] if not current_matches.empty else None

    current_slope = None
    if current_reach_row is not None:
        try:
            s = float(current_reach_row["slope"])
            if np.isfinite(s):
                current_slope = s
        except (KeyError, TypeError, ValueError):
            pass

    fallback = (current_slope, str(reach_id)) if current_slope is not None else (DEFAULT_ND_SLOPE, None)

    try:
        ds_xs_geom = _ds_xs_geometry(rfc, ras_xs_data)
    except (KeyError, TypeError, ValueError) as e:
        logging.warning(
            "Could not resolve downstream XS geometry for %s; falling back. Error: %s",
            reach_id, e,
        )
        return fallback

    candidates = rfc.nwm_reaches[rfc.nwm_reaches.intersects(ds_xs_geom)]
    if candidates.empty:
        logging.warning("No NWM reach intersects downstream XS for %s; falling back", reach_id)
        return fallback

    selected = _select_ds_slope_reach(candidates, reach_id, rfc.nwm_walker.tree_dict)
    if selected is None:
        logging.warning(
            "No candidate on downstream path for %s; falling back to current reach slope or default",
            reach_id,
        )
        return fallback

    try:
        slope = float(selected["slope"])
        if np.isfinite(slope):
            return slope, str(selected["ID"])
    except (KeyError, TypeError, ValueError):
        pass

    logging.warning(
        "No valid slope data for downstream boundary reach %s; falling back",
        selected["ID"],
    )
    return fallback


def validate_reach_conflation(reach_xs_data: dict, reach_id: str):
    """Raise error for invalid conflation.

    The trim_reach method in subset_gpkg.py will return an empty geodataframe when u/s xs_id is lower than d/s xs_id.
    This likely indicates poor CRS inference.
    """
    if reach_xs_data["eclipsed"]:
        return  # eclipsed reaches always pass
    us = reach_xs_data["us_xs"]
    ds = reach_xs_data["ds_xs"]
    if (us["river"] == ds["river"]) & (us["reach"] == ds["reach"]) & (us["xs_id"] < ds["xs_id"]):
        err_str = f"Reach {reach_id} has u/s xs station ({us['xs_id']}) lower than d/s xs station ({ds['xs_id']})"
        raise BadConflation(err_str)


def ras_reaches_metadata(rfc: RasFimConflater, candidate_reaches: gpd.GeoDataFrame):
    """Return the metadata for the RAS reaches."""
    reach_metadata = OrderedDict()
    candidate_reaches = rfc.nwm_reaches[rfc.nwm_reaches["ID"].isin(candidate_reaches)]
    for reach in candidate_reaches.itertuples():
        try:
            # get the xs data for the reach
            ras_xs_data = map_reach_xs(rfc, reach)
            if ras_xs_data is not None:
                validate_reach_conflation(ras_xs_data, str(reach.ID))
                metadata = rfc.get_nwm_reach_metadata(reach.ID)
                ds_slope, ds_slope_source_nwm_id = get_ds_boundary_slope(rfc, reach.ID, ras_xs_data)
                metadata["ds_slope"] = _clamp_nd_slope(float(f"{ds_slope:.{ND_SLOPE_SIG_FIGS}g}"))
                if ds_slope_source_nwm_id is not None:
                    metadata["ds_slope_source_nwm_id"] = ds_slope_source_nwm_id
                reach_metadata[reach.ID] = ras_xs_data | metadata
        except Exception as e:
            logging.error(f"network id: {reach.ID} | Error: {e}")
            logging.error(f"network id: {reach.ID} | Traceback: {traceback.format_exc()}")

    return reach_metadata
