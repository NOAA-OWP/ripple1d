"""Utility functions for the hecstac ras module."""

import logging
import os
import re
from functools import wraps
from io import BytesIO
from pathlib import Path
from typing import Callable

import contextily as ctx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from pyproj import CRS
from shapely import lib
from shapely.errors import UnsupportedGEOSVersionError
from shapely.geometry import LineString, MultiPoint, Point

from ripple1d.hecstac.common.base_io import ModelFileReader
from ripple1d.hecstac.common.s3_utils import save_bytes_s3


def export_thumbnail(layers: list[Callable], title: str, crs: CRS, filepath: str):
    """Generate a thumbnail and save it."""
    fig, ax = plt.subplots(figsize=(12, 12))

    # Add data
    legend_handles = []
    for layer in layers:
        try:
            legend_handles += layer(ax)
        except Exception:
            continue

    # Add OpenStreetMap basemap
    try:
        ctx.add_basemap(ax, crs=crs, source=ctx.providers.OpenStreetMap.Mapnik)
    except Exception as e:
        pass

    # Formatting
    ax.set_title(title)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.legend(handles=legend_handles, loc="center left", bbox_to_anchor=(1, 0.5))
    fig.tight_layout()

    # Save
    if filepath.startswith("s3://"):
        img_data = BytesIO()
        fig.savefig(img_data, format="png", bbox_inches="tight")
        img_data.seek(0)
        save_bytes_s3(img_data, filepath)
    else:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        fig.savefig(filepath, dpi=80, bbox_inches="tight")

    # Close fig
    plt.close(fig)


def find_model_files(ras_prj: str) -> list[str]:
    # TODO: Add option to recursively iterate through all subdirectories in a model folder.
    # TODO: Add option to search for files on S3.
    """Find all files with the same base name and return absolute paths."""
    ras_prj = Path(ras_prj).resolve()
    parent = ras_prj.parent
    stem = ras_prj.stem
    return [str(i.resolve()) for i in parent.glob(f"{stem}*")]


def is_ras_prj(url: str) -> bool:
    """Check if a file is a HEC-RAS project file."""
    file_str = ModelFileReader(url).content
    if "Proj Title" in file_str.split("\n")[0]:
        return True
    else:
        return False


def search_contents(
    lines: list[str],
    search_string: str,
    token: str = "=",
    expect_one: bool = True,
    require_one: bool = True,
    regex: bool = False,
) -> list[str] | str:
    """Split a line by a token and returns the second half of the line if the search_string is found in the first half.

    The regex option assumes that the token is included in the regex.
    """
    if regex:
        matches = lambda x: re.match(search_string, x)
    else:
        matches = lambda x: f"{search_string}{token}" in x
    results = []
    for line in lines:
        if matches(line):
            val = line.split(token)[1]
            if val != "":
                results.append(val)

    if expect_one and len(results) > 1:
        raise ValueError(f"expected 1 result for {search_string}, got {len(results)} results")
    elif require_one and len(results) == 0:
        raise ValueError(f"1 result for {search_string} is required, no results found")
    elif expect_one and len(results) == 1:
        return results[0]
    else:
        return results


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


def text_block_from_start_str_length(start_str: str, number_of_lines: int, lines: list[str]) -> list[str]:
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


def data_pairs_from_text_block(lines: list[str], width: int) -> list[tuple[float, float]]:
    """Split lines at given width to get paired data string. Split the string in half and convert to tuple of floats."""
    pairs = []
    for line in lines:
        if line == "               .               .":
            continue
        for i in range(0, len(line), width):
            x = line[i : int(i + width / 2)]
            y = line[int(i + width / 2) : int(i + width)]
            pairs.append((float(x), float(y)))

    return pairs


def delimited_pairs_to_lists(lines: list[str]) -> tuple[list[float], list[float]]:
    """Extract subdivisions from the manning's text block."""
    stations = []
    mannings = []
    for line in lines:
        pairs = line.split("       0")
        for p in pairs[:-1]:
            station = float(p[:8])
            n = float(p[8:])
            stations.append(station)
            mannings.append(n)
    return (stations, mannings)


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
            continue
    return cross_sections.loc[cross_sections["river_reach_rs"].isin(river_reach_rs)]


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


class RequireGeos:
    """Unsure."""

    def __init__(self, version):
        if version.count(".") != 2:
            raise ValueError("Version must be <major>.<minor>.<patch> format")
        self.version = tuple(int(x) for x in version.split("."))

    def __call__(self, func):
        """Call."""
        is_compatible = lib.geos_version >= self.version
        is_doc_build = os.environ.get("SPHINX_DOC_BUILD") == "1"  # set in docs/conf.py
        if is_compatible and not is_doc_build:
            return func  # return directly, do not change the docstring

        msg = "'{}' requires at least GEOS {}.{}.{}.".format(func.__name__, *self.version)
        if is_compatible:

            @wraps(func)
            def wrapped(*args, **kwargs):
                return func(*args, **kwargs)

        else:

            @wraps(func)
            def wrapped(*args, **kwargs):
                raise UnsupportedGEOSVersionError(msg)

        doc = wrapped.__doc__
        if doc:
            # Insert the message at the first double newline
            position = doc.find("\n\n") + 2
            # Figure out the indentation level
            indent = 0
            while True:
                if doc[position + indent] == " ":
                    indent += 1
                else:
                    break
            wrapped.__doc__ = doc.replace("\n\n", "\n\n{}.. note:: {}\n\n".format(" " * indent, msg), 1)

        return wrapped


def multithreading_enabled(func):
    """
    Prepare multithreading by setting the writable flags of object type ndarrays to False.

    NB: multithreading also requires the GIL to be released, which is done in
    the C extension (ufuncs.c).
    """

    @wraps(func)
    def wrapped(*args, **kwargs):
        array_args = [arg for arg in args if isinstance(arg, np.ndarray) and arg.dtype == object] + [
            arg
            for name, arg in kwargs.items()
            if name not in {"where", "out"} and isinstance(arg, np.ndarray) and arg.dtype == object
        ]
        old_flags = [arr.flags.writeable for arr in array_args]
        try:
            for arr in array_args:
                arr.flags.writeable = False
            return func(*args, **kwargs)
        finally:
            for arr, old_flag in zip(array_args, old_flags):
                arr.flags.writeable = old_flag

    return wrapped


@RequireGeos("3.7.0")
@multithreading_enabled
def reverse(geometry, **kwargs):
    """Return a copy of a Geometry with the order of coordinates reversed.

    If a Geometry is a polygon with interior rings, the interior rings are also
    reversed.

    Points are unchanged. None is returned where Geometry is None.

    Parameters
    ----------
    geometry : Geometry or array_like
    **kwargs
        See `NumPy ufunc docs <ufuncs.kwargs>` for other keyword arguments.

    See Also
    --------
    is_ccw : Checks if a Geometry is clockwise.

    Examples
    --------
    >>> from shapely import LineString, Polygon
    >>> reverse(LineString([(0, 0), (1, 2)]))
    <LINESTRING (1 2, 0 0)>
    >>> reverse(Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]))
    <POLYGON ((0 0, 0 1, 1 1, 1 0, 0 0))>
    >>> reverse(None) is None
    True
    """
    return lib.reverse(geometry, **kwargs)
