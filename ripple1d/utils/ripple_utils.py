"""General utils used by ripple1d."""

from __future__ import annotations

import glob
import logging
import os
from collections import defaultdict
from copy import copy
from functools import lru_cache
from pathlib import Path
from typing import Optional

import boto3
import fiona
import geopandas as gpd
import numpy as np
import pandas as pd
from dotenv import find_dotenv, load_dotenv
from pyproj import CRS
from shapely import (
    GeometryCollection,
    LineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    buffer,
    concave_hull,
    line_merge,
    make_valid,
    reverse,
    union_all,
)
from shapely.ops import split, substring

from ripple1d.consts import DEFAULT_MAX_WALK
from ripple1d.errors import (
    InvalidNetworkPath,
    RASComputeError,
    RASComputeMeshError,
    RASGeometryError,
    RASStoreAllMapsError,
)
from ripple1d.utils.s3_utils import list_keys

load_dotenv(find_dotenv())


def determine_crs_units(crs: CRS):
    """Determine the units of the crs."""
    if type(crs) not in [str, int, CRS]:
        raise TypeError(f"expected either pyproj.CRS, wkt(st), or epsg code(int); recieved {type(crs)} ")

    unit_name = CRS(crs).axis_info[0].unit_name
    if crs.axis_info[0].unit_name not in ["degree", "US survey foot", "foot", "metre"]:
        raise ValueError(
            f"Expected the crs units to be one of degree, US survey foot, foot, or metre; recieved {unit_name}"
        )

    return unit_name


def clip_ras_centerline(centerline: LineString, xs: gpd.GeoDataFrame, buffer_distance: float = 0):
    """Clip RAS centeline to the most upstream and downstream cross sections."""
    us_xs, ds_xs = us_ds_xs(xs)

    us_offset = us_xs.offset_curve(-buffer_distance)
    ds_offset = ds_xs.offset_curve(buffer_distance)

    if centerline.intersects(us_offset):
        start_station = centerline.project(validate_point(centerline.intersection(us_offset)))
    else:
        start_station = 0

    if centerline.intersects(ds_offset):
        stop_station = centerline.project(validate_point(centerline.intersection(ds_offset)))
    else:
        stop_station = centerline.length

    return substring(centerline, start_station, stop_station)


def us_ds_xs(xs: gpd.GeoDataFrame):
    """Get most upstream and downstream cross sections."""
    return (
        xs[xs["river_station"] == xs["river_station"].max()].geometry.iloc[0],
        xs[xs["river_station"] == xs["river_station"].min()].geometry.iloc[0],
    )


def prj_is_ras(path: str):
    """Verify if prj is from hec-ras model."""
    with open(path) as f:
        prj_contents = f.read()
    if "Proj Title" in prj_contents.split("\n")[0]:
        return True
    else:
        return False


def decode(df: pd.DataFrame):
    """Decode all string columns in a pandas DataFrame."""
    for c in df.columns:
        df[c] = df[c].str.decode("utf-8")
    return df


def get_path(expected_path: str, client: boto3.client = None, bucket: str = None) -> str:
    """Get the path for a file."""
    if client and bucket:
        path = Path(expected_path)
        prefix = path.parent.as_posix().replace("s3:/", "s3://")
        paths = list_keys(client, bucket, prefix, path.suffix)
        if not paths:
            paths = list_keys(client, bucket, prefix, path.suffix.upper())
    else:
        prefix = os.path.dirname(expected_path)
        paths = glob.glob(rf"{prefix}\*{os.path.splitext(expected_path)[1]}")
        if not paths:
            paths = glob.glob(rf"{prefix}\*{os.path.splitext(expected_path)[1].upper()}")

    if expected_path in paths:
        return expected_path
    else:
        for path in paths:
            if path.endswith(Path(expected_path).suffix.upper()):
                return path


def fix_reversed_xs(xs: gpd.GeoDataFrame, river: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Check if cross sections are drawn right to left looking downstream. If not reverse them."""
    subsets = []
    for _, reach in river.iterrows():
        subset_xs = xs.loc[xs["river_reach"] == reach["river_reach"]]
        not_reversed_xs = check_xs_direction(subset_xs, reach.geometry)
        subset_xs["geometry"] = subset_xs.apply(
            lambda row: (
                row.geometry
                if row["river_reach_rs"] in list(not_reversed_xs["river_reach_rs"])
                else reverse(row.geometry)
            ),
            axis=1,
        )
        subsets.append(subset_xs)
    return pd.concat(subsets)


def validate_point(geom):
    """Validate that point is of type Point. If Multipoint or Linestring create point from first coordinate pair."""
    if isinstance(geom, Point):
        return geom
    elif isinstance(geom, MultiPoint):
        return geom.geoms[0]
    elif isinstance(geom, LineString) and list(geom.coords):
        return Point(geom.coords[0])
    elif geom.is_empty:
        raise IndexError(f"expected point at xs-river intersection got: {type(geom)} | {geom}")
    else:
        raise TypeError(f"expected point at xs-river intersection got: {type(geom)} | {geom}")


def check_xs_direction(cross_sections: gpd.GeoDataFrame, reach: LineString):
    """Return only cross sections that are drawn right to left looking downstream."""
    river_reach_rs = []
    for _, xs in cross_sections.iterrows():
        try:
            point = reach.intersection(xs["geometry"])
            point = validate_point(point)
            xs_rs = reach.project(point)

            offset = xs.geometry.offset_curve(-1)
            if reach.intersects(offset):  # if the offset line intersects then use this logic
                point = reach.intersection(offset)
                point = validate_point(point)

                offset_rs = reach.project(point)
                if xs_rs > offset_rs:
                    river_reach_rs.append(xs["river_reach_rs"])
            else:  # if the original offset line did not intersect then try offsetting the other direction and applying
                # the opposite stationing logic; the orginal line may have gone beyound the other line.
                offset = xs.geometry.offset_curve(1)
                point = reach.intersection(offset)
                point = validate_point(point)

                offset_rs = reach.project(point)
                if xs_rs < offset_rs:
                    river_reach_rs.append(xs["river_reach_rs"])

        except IndexError as e:
            logging.debug(
                f"cross section does not intersect river-reach: {xs['river']} {xs['reach']} {xs['river_station']}: error: {e}"
            )
            continue
    return cross_sections.loc[cross_sections["river_reach_rs"].isin(river_reach_rs)]


def xs_concave_hull(xs_df: gpd.GeoDataFrame, junction: gpd.GeoDataFrame = None) -> gpd.GeoDataFrame:
    """Compute and return the concave hull (polygon) for a set of cross sections (lines all facing the same direction)."""
    polygons = []
    if len(xs_df) <= 0:
        return None
    assert not all(
        [i.is_empty for i in xs_df.geometry]
    ), "No valid cross-sections found.  Possibly non-georeferenced model"
    assert len(xs_df) > 1, "Only one valid cross-section found."
    for river_reach in xs_df["river_reach"].unique():
        xs_subset = xs_df[xs_df["river_reach"] == river_reach].sort_values("river_station")
        for i in range(len(xs_subset) - 1):
            first = xs_subset.iloc[i]
            last = xs_subset.iloc[i + 1]
            first_pts = [Point(coord) for coord in first["geometry"].coords]
            last_pts = [Point(coord) for coord in last["geometry"].coords]
            if not LineString([first_pts[0], last_pts[0]]).intersects(LineString([first_pts[-1], last_pts[-1]])):
                poly = Polygon(last_pts + first_pts[::-1])
            else:
                poly = Polygon(last_pts + first_pts)
            if poly.is_valid:
                polygons.append(poly)
            else:
                polygons.append(make_valid(poly))

    if junction is not None:
        for _, j in junction.iterrows():
            polygons.append(junction_hull(xs_df, j))
    out_hull = clean_polygons(polygons)
    return gpd.GeoDataFrame({"geometry": [out_hull]}, geometry="geometry", crs=xs_df.crs)


def clean_polygons(polygons: list) -> list:
    """Unpack geometry collections, make polygons valid, and remove internal rings."""
    polys = unpack_geoms(polygons)
    polys = [make_valid(i) for i in polys]
    unioned = union_all(polys)
    unioned = remove_holes(unioned)
    return unioned


def unpack_geoms(geoms: list):
    """Unpack multipolygons and geometry collections to polygon list."""
    out = []
    for i in geoms:
        if isinstance(i, GeometryCollection):
            for j in i.geoms:
                out.extend(unpack_geoms([j]))
        elif isinstance(i, MultiPolygon):
            out.extend([j for j in i.geoms])
        else:
            out.append(i)
    return out


def remove_holes(geom):
    """Remove internal holes in polygons."""
    if isinstance(geom, Polygon):
        return Polygon(geom.exterior)
    elif isinstance(geom, MultiPolygon):
        return MultiPolygon([Polygon(p.exterior) for p in geom.geoms])
    return geom


def determine_junction_xs(xs: gpd.GeoDataFrame, junction: gpd.GeoSeries) -> gpd.GeoDataFrame:
    """Determine the cross sections that bound a junction."""
    junction_xs = []
    for us_river, us_reach in zip(junction.us_rivers.split(","), junction.us_reaches.split(",")):
        xs_us_river_reach = xs[(xs["river"] == us_river) & (xs["reach"] == us_reach)]
        junction_xs.append(
            xs_us_river_reach[xs_us_river_reach["river_station"] == xs_us_river_reach["river_station"].min()]
        )
    for ds_river, ds_reach in zip(junction.ds_rivers.split(","), junction.ds_reaches.split(",")):
        xs_ds_river_reach = xs[(xs["river"] == ds_river) & (xs["reach"] == ds_reach)]
        xs_ds_river_reach["geometry"] = xs_ds_river_reach.reverse()
        junction_xs.append(
            xs_ds_river_reach[xs_ds_river_reach["river_station"] == xs_ds_river_reach["river_station"].max()]
        )
    return pd.concat(junction_xs)


def determine_xs_order(row: gpd.GeoSeries, junction_xs: gpd.gpd.GeoDataFrame):
    """Detemine what order cross sections bounding a junction should be in to produce a valid polygon."""
    candidate_lines = junction_xs[junction_xs["river_reach_rs"] != row["river_reach_rs"]]
    candidate_lines["distance"] = candidate_lines["start"].distance(row.end)
    return candidate_lines.loc[candidate_lines["distance"] == candidate_lines["distance"].min(), "river_reach_rs"].iloc[
        0
    ]


def junction_hull(xs: gpd.GeoDataFrame, junction: gpd.GeoSeries) -> gpd.GeoDataFrame:
    """Compute and return the concave hull (polygon) for a juction."""
    junction_xs = determine_junction_xs(xs, junction)

    junction_xs["start"] = junction_xs.apply(lambda row: row.geometry.boundary.geoms[0], axis=1)
    junction_xs["end"] = junction_xs.apply(lambda row: row.geometry.boundary.geoms[1], axis=1)
    junction_xs["to_line"] = junction_xs.apply(lambda row: determine_xs_order(row, junction_xs), axis=1)

    coords = []
    first_to_line = junction_xs["to_line"].iloc[0]
    to_line = first_to_line
    while True:
        xs = junction_xs[junction_xs["river_reach_rs"] == to_line]
        coords += list(xs.iloc[0].geometry.coords)
        to_line = xs["to_line"].iloc[0]
        if to_line == first_to_line:
            break
    return Polygon(coords)


def search_contents(lines: list, search_string: str, token: str = "=", expect_one: bool = True) -> list[str]:
    """Split a line by a token and returns the second half of the line if the search_string is found in the first half."""
    results = []
    for line in lines:
        if f"{search_string}{token}" in line:
            results.append(line.split(token)[1])

    if expect_one and len(results) > 1:
        raise ValueError(f"expected 1 result, got {len(results)}")
    elif expect_one and len(results) == 0:
        raise ValueError("expected 1 result, no results found")
    elif expect_one and len(results) == 1:
        return results[0]
    else:
        return results


def replace_line_in_contents(lines: list, search_string: str, replacement: str, token: str = "="):
    """Split a line by a token and replaces the second half of the line (for the first occurence only!)."""
    updated = 0
    for i, line in enumerate(lines):
        if f"{search_string}{token}" in line:
            updated += 1
            lines[i] = line.split(token)[0] + token + replacement
    if updated == 0:
        raise ValueError(f"search_string: {search_string} not found in lines")
    elif updated > 1:
        raise ValueError(f"expected 1 result, got {updated} occurences of {replacement}")
    else:
        return lines


def text_block_from_start_end_str(
    start_str: str, end_strs: list[str], lines: list, additional_lines: int = 0
) -> list[str]:
    """Search for an exact match to the start_str and return all lines from there to a line that contains the end_str."""
    start_str = handle_spaces(start_str, lines)

    start_index = lines.index(start_str)
    end_index = len(lines)
    for line in lines[start_index + 1 :]:
        if end_index != len(lines):
            break
        for end_str in end_strs:
            if end_str in line:
                end_index = lines.index(line) + additional_lines
                break
    return lines[start_index:end_index]


def text_block_from_start_str_to_empty_line(start_str: str, lines: list) -> list[str]:
    """Search for an exact match to the start_str and return all lines from there to the next empty line."""
    start_str = handle_spaces(start_str, lines)
    results = []
    in_block = False
    for line in lines:
        if line == start_str:
            in_block = True
            results.append(line)
            continue

        if in_block:
            if line == "":
                results.append(line)
                return results
            else:
                results.append(line)
    return results


def text_block_from_start_str_length(start_str: str, number_of_lines: int, lines: list) -> list[str]:
    """Search for an exact match to the start token and return a number of lines equal to number_of_lines."""
    start_str = handle_spaces(start_str, lines)
    results = []
    in_block = False
    for line in lines:
        if line == start_str:
            in_block = True
            continue
        if in_block:
            if len(results) >= number_of_lines:
                return results
            else:
                results.append(line)


def data_pairs_from_text_block(lines: list[str], width: int) -> list[tuple[float]]:
    """Split lines at given width to get paired data string. Split the string in half and convert to tuple of floats."""
    pairs = []
    for line in lines:
        for i in range(0, len(line), width):
            x = line[i : int(i + width / 2)]
            y = line[int(i + width / 2) : int(i + width)]
            try:
                pairs.append((float(x), float(y)))
            except ValueError:  # If a user has left a coordinate blank, skip point
                continue

    return pairs


def data_triplets_from_text_block(lines: list[str], width: int) -> list[tuple[float]]:
    """Split lines at given width to get paired data string. Split the string in half and convert to tuple of floats."""
    pairs = []
    for line in lines:
        for i in range(0, len(line), width):
            x = line[i : int(i + width / 3)]
            y = line[int(i + width / 3) : int(i + (width * 2 / 3))]
            z = line[int(i + (width * 2 / 3)) : int(i + (width))]
            pairs.append((float(x), float(y), float(z)))

    return pairs


def handle_spaces(line: str, lines: list[str]):
    """Handle spaces in the line."""
    if line in lines:
        return line
    elif handle_spaces_arround_equals(line.rstrip(" "), lines):
        return handle_spaces_arround_equals(line.rstrip(" "), lines)
    elif handle_spaces_arround_equals(line + " ", lines) in lines:
        return handle_spaces_arround_equals(line + " ", lines)
    else:
        raise ValueError(f"line: {line} not found in lines")


def handle_spaces_arround_equals(line: str, lines: list[str]) -> str:
    """Handle spaces in the line."""
    if line in lines:
        return line
    elif "= " in line:
        if line.replace("= ", "=") in lines:
            return line.replace("= ", "=")
    else:
        return line.replace("=", "= ")


def assert_no_mesh_error(compute_message_file: str, require_exists: bool):
    """Scan *.computeMsgs.txt for errors encountered. Raise RASComputeMeshError if found."""
    try:
        with open(compute_message_file) as f:
            content = f.read()
    except FileNotFoundError:
        if require_exists:
            raise
    else:
        for line in content.splitlines():
            if "error generating mesh" in line.lower():
                raise RASComputeMeshError(
                    f"'error generating mesh' found in {compute_message_file}. Full file content:\n{content}\n^^^ERROR^^^"
                )


def assert_no_ras_geometry_error(compute_message_file: str):
    """Scan *.computeMsgs.txt for errors encountered."""
    with open(compute_message_file) as f:
        content = f.read()
    for line in content.splitlines():
        if "geometry writer failed" in line.lower() or "error processing geometry" in line.lower():
            raise RASGeometryError(
                f"geometry error found in {compute_message_file}. Full file content:\n{content}\n^^^ERROR^^^"
            )


def assert_no_ras_compute_error_message(compute_message_file: str):
    """Scan *.computeMsgs.txt for errors encountered."""
    with open(compute_message_file) as f:
        content = f.read()
    for line in content.splitlines():
        if "ERROR:" in line:
            raise RASComputeError(
                f"'ERROR:' found in {compute_message_file}. Full file content:\n{content}\n^^^ERROR^^^"
            )


def assert_no_store_all_maps_error_message(compute_message_file: str):
    """Scan *.computeMsgs.txt for errors encountered."""
    with open(compute_message_file) as f:
        content = f.read()
    for line in content.splitlines():
        if "error executing: storeallmaps" in line.lower():
            raise RASStoreAllMapsError(
                f"{repr(line)} found in {compute_message_file}. Full file content:\n{content}\n^^^ERROR^^^"
            )


def resample_vertices(stations: np.ndarray, max_interval: float) -> np.ndarray:
    """Resample a set of stations so that no gaps are larger than max_interval."""
    i = 0
    max_iter = 1e10
    while i < (len(stations) - 1) and i < max_iter:
        gap = stations[i + 1] - stations[i]
        if gap <= max_interval:
            i += 1
            continue
        subdivisions = (gap // max_interval) - 1
        modulo = gap - (subdivisions * max_interval)
        new_pts = np.arange(stations[i] + (modulo / 2), stations[i + 1], max_interval)
        stations = np.insert(stations, i + 1, new_pts)
        i += len(new_pts) + 1
    return stations


class NetworkWalker:
    """Walks networks from upstream to downstream (Parent class)."""

    def __init__(self, network_path: str, max_iter: int = 100):
        self.network_path: str = network_path
        self.max_iter: int = max_iter

    def walk(self, us_id: str | int, ds_id: Optional[str | int] = None) -> tuple[str]:
        """Attempt to find a path from us_id to ds_id.  If ds_id is none, walk till self.max_iter."""
        cur_id = copy(us_id)
        path = []
        _iter = 0
        while True:
            path.append(cur_id)
            if cur_id == ds_id:
                break
            elif _iter > self.max_iter or cur_id not in self.tree_dict:
                if ds_id is None:
                    return path
                else:
                    raise InvalidNetworkPath(us_id, ds_id, cur_id, _iter)
            else:
                _iter += 1
                cur_id = self.tree_dict[cur_id]  # move to next d/s reach
        return tuple(path)

    def are_connected(self, us_id, ds_id) -> bool:
        """Check if two reaches are hydrologically connected."""
        try:
            self.walk(us_id, ds_id)
        except InvalidNetworkPath:
            return False
        else:
            return True

    @property
    @lru_cache
    def tree_dict(self) -> dict:
        """Placeholder for children."""
        return {}

    @property
    @lru_cache
    def tree_dict_us(self) -> dict:
        """Dictionary mapping downstream reach to parents (list)."""
        tree_dict_us = defaultdict(list)
        for k, v in self.tree_dict.items():
            tree_dict_us[v].append(k)
        return tree_dict_us

    def get_confluence(self, a: int | str, b: int | str) -> int | str:
        """Find the first reach where paths meet.  Otherwise return None."""
        path_a = self.walk(a)
        path_b = self.walk(b)
        common = set(path_a).intersection(path_b)
        for i in path_a:
            if i in common:
                return i


class NWMWalker(NetworkWalker):
    """Subclass to walk the National Water Model network."""

    ID_COL: str = "ID"
    TO_ID_COL: str = "to_id"

    def __init__(
        self,
        network_path: str,
        max_iter: int = DEFAULT_MAX_WALK,
        network_df: Optional[pd.DataFrame | gpd.GeoDataFrame] = None,
    ):
        self.max_iter: int = max_iter
        if network_df is not None:
            self.df: pd.DataFrame = network_df[[self.ID_COL, self.TO_ID_COL]]
        else:
            self.df: pd.DataFrame = pd.read_parquet(network_path, columns=[self.ID_COL, self.TO_ID_COL])

    @property
    @lru_cache
    def tree_dict(self) -> dict:
        """Dictionary mapping tributary to downstream reach."""
        return dict(zip(self.df[self.ID_COL], self.df[self.TO_ID_COL]))


class RASWalker(NetworkWalker):
    """Subclass to walk a HEC-RAS model."""

    JUNCTION_LAYER_ID: str = "Junction"

    @property
    @lru_cache
    def gdf(self) -> gpd.GeoDataFrame:
        """Load the network from a file."""
        if "Junction" in fiona.listlayers(self.network_path):
            source_junction = gpd.read_file(self.network_path, layer=self.JUNCTION_LAYER_ID)
            return source_junction
        else:
            return gpd.GeoDataFrame()

    @property
    @lru_cache
    def tree_dict(self) -> dict:
        """Dictionary mapping tributary to downstream reach."""
        return self.parse_junction_table(output="reach")

    @property
    @lru_cache
    def dist_dict(self) -> dict:
        """Dictionary mapping tributary to downstream reach."""
        return self.parse_junction_table(output="distance")

    def parse_junction_table(self, output: str = "reach") -> dict:
        """Parse the junction table for either downstream reaches or downstream distances."""
        tree_dict = {}
        for ind, r in self.gdf.iterrows():
            trib_rivers = r["us_rivers"].split(",")
            trib_reaches = r["us_reaches"].split(",")
            if output == "reach":
                target = [f'{r["ds_rivers"].ljust(16)},{r["ds_reaches"].ljust(16)}'] * 2
            elif output == "distance":
                target = [float(i) for i in r["junction_lengths"].split(",")]
            for riv, rch, t in zip(trib_rivers, trib_reaches, target):
                tree_dict[f"{riv.ljust(16)},{rch.ljust(16)}"] = t
        return tree_dict

    @lru_cache
    def reach_distance_modifiers(self, path: tuple[str]) -> dict:
        """Make a dictionary mapping river_reach to cumulative station increase across the path due to junctions."""
        offset = 0
        distance_dict = {i: 0 for i in path}  # intialize zeros
        path = path[:-1]  # remove first reach because any junction length there is irrelevant
        for river_reach in path[::-1]:  # walk d/s to u/s
            offset += self.dist_dict[river_reach]
            distance_dict[river_reach] = offset
        return distance_dict
