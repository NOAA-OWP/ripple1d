"""Contains classes and methods to parse HEC-RAS files."""

import datetime
import math
import os
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import Iterator, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from rashdf import RasGeomHdf, RasHdf, RasPlanHdf
from shapely import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    buffer,
    make_valid,
    union_all,
)
from shapely.ops import unary_union

from ripple1d.hecstac.common.base_io import ModelFileReader
from ripple1d.hecstac.ras.errors import InvalidStructureDataError
from ripple1d.hecstac.ras.utils import (
    check_xs_direction,
    data_pairs_from_text_block,
    data_triplets_from_text_block,
    delimited_pairs_to_lists,
    reverse,
    search_contents,
    text_block_from_start_end_str,
    text_block_from_start_str_length,
    text_block_from_start_str_to_empty_line,
    validate_point,
)


def name_from_suffix(fpath: str, suffix: str) -> str:
    """Generate a name by appending a suffix to the file stem."""
    return f"{Path(fpath).stem}.{suffix.strip(' ')}"


class CachedFile:
    """Base class for caching and initialization of file-based assets."""

    _cache = {}  # Class-level cache for instances

    def __new__(cls, fpath):
        """Override __new__ to implement caching."""
        if fpath in cls._cache:
            return cls._cache[fpath]
        instance = super().__new__(cls)
        cls._cache[fpath] = instance
        return instance

    def __init__(self, fpath):
        """Prevent reinitialization if the instance is already cached."""
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True
        self.fpath = fpath
        self.model_file = ModelFileReader(self.fpath)
        self.file_lines = self.model_file.content.splitlines()


class River:
    """HEC-RAS River."""

    def __init__(self, river: str, reaches: list[str] = None):
        self.river = river
        self.reaches = reaches or []


class XS:
    """HEC-RAS Cross Section."""

    def __init__(
        self,
        ras_data: list[str],
        river_reach: str,
        river: str,
        reach: str,
        reach_geom: LineString = None,  # TODO: Does adding this to every section create a massive memory footprint?
    ):
        self.ras_data = ras_data
        self.river = river
        self.reach = reach
        self.river_reach = river_reach
        self.river_reach_rs = f"{river} {reach} {self.river_station}"
        self.river_reach_rs_str = f"{river} {reach} {self.river_station_str}"
        self._is_interpolated: bool | None = None
        self.reach_geom: LineString = reach_geom
        self.has_lateral_structures = False
        self.computed_channel_reach_length = None
        self.computed_channel_reach_length_ratio = None
        self.thalweg_drop = None

    def split_xs_header(self, position: int):
        """Split cross section header.

        Example: Type RM Length L Ch R = 1 ,83554.  ,237.02,192.39,113.07.
        """
        header = search_contents(self.ras_data, "Type RM Length L Ch R ", expect_one=True)
        val = header.split(",")[position]
        if val == "":
            return "0"
        else:
            return val

    @cached_property
    def river_station(self) -> float:
        """Cross section river station."""
        return float(self.split_xs_header(1).replace("*", ""))

    @cached_property
    def river_station_str(self) -> str:
        """Return the river station with * for interpolated sections."""
        return self.split_xs_header(1).rstrip()

    @cached_property
    def left_reach_length(self) -> float:
        """Cross section left reach length."""
        return float(self.split_xs_header(2))

    @cached_property
    def channel_reach_length(self) -> float:
        """Cross section channel reach length."""
        return float(self.split_xs_header(3))

    @cached_property
    def right_reach_length(self) -> float:
        """Cross section right reach length."""
        return float(self.split_xs_header(4))

    @cached_property
    def number_of_coords(self) -> int:
        """Number of coordinates in cross section."""
        try:
            return int(search_contents(self.ras_data, "XS GIS Cut Line", expect_one=True))
        except ValueError:
            return 0

    @cached_property
    def min_elevation(self) -> float:
        """The min elevaiton in the cross section."""
        if self.station_elevation_points:
            _, y = list(zip(*self.station_elevation_points))
            return min(y)

    @cached_property
    def xs_max_elevation(self) -> float | None:
        """Cross section maximum elevation."""
        if self.station_elevation_points:
            _, y = list(zip(*self.station_elevation_points))
            return max(y)

    @cached_property
    def min_elevation_in_channel(self):
        """A boolean indicating if the minimum elevation is in the channel."""
        if self.min_elevation == self.thalweg:
            return True
        else:
            return False

    @cached_property
    def thalweg(self):
        """The min elevation of the channel (between bank points)."""
        return self.station_elevation_df.loc[
            (self.station_elevation_df["Station"] <= self.right_bank_station)
            & (self.station_elevation_df["Station"] >= self.left_bank_station),
            "Elevation",
        ].min()

    @cached_property
    def station_length(self):
        """Length of cross section based on station-elevation data."""
        return self.last_station - self.first_station

    @cached_property
    def first_station(self):
        """First station of the cross section."""
        return float(self.station_elevation_points[0][0])

    @cached_property
    def last_station(self):
        """Last station of the cross section."""
        return float(self.station_elevation_points[-1][0])

    @cached_property
    def xs_length_ratio(self):
        """Ratio of the cutline length to the station length."""
        if self.skew:
            return self.cutline_length / (self.station_length / math.cos(math.radians(self.skew)))
        else:
            return self.cutline_length / self.station_length

    @cached_property
    def coords(self) -> list[tuple[float, float]] | None:
        """Cross section coordinates."""
        lines = text_block_from_start_str_length(
            f"XS GIS Cut Line={self.number_of_coords}",
            math.ceil(self.number_of_coords / 2),
            self.ras_data,
        )
        if lines:
            return data_pairs_from_text_block(lines, 32)

    @cached_property
    def geom(self):
        """Geometry of the cross section according to its coords."""
        return LineString(self.coords)

    @cached_property
    def number_of_station_elevation_points(self) -> int:
        """Number of station elevation points."""
        return int(search_contents(self.ras_data, "#Sta/Elev", expect_one=True))

    @cached_property
    def station_elevation_points(self) -> list[tuple[float, float]] | None:
        """Station elevation points."""
        try:
            lines = text_block_from_start_str_length(
                f"#Sta/Elev= {self.number_of_station_elevation_points} ",
                math.ceil(self.number_of_station_elevation_points / 5),
                self.ras_data,
            )
            return data_pairs_from_text_block(lines, 16)
        except ValueError:
            return None

    @cached_property
    def bank_stations(self) -> list[str]:
        """Bank stations."""
        return search_contents(self.ras_data, "Bank Sta", expect_one=True, require_one=False).split(",")

    @cached_property
    def banks_encompass_channel(self):
        """A boolean; True if the channel centerlien intersects the cross section between the bank stations."""
        if self.cross_section_intersects_reach:
            if (self.centerline_intersection_station + self.first_station) < self.right_bank_station and (
                self.centerline_intersection_station + self.first_station
            ) > self.left_bank_station:
                return True
            else:
                return False

    def set_computed_reach_length(self, computed_river_station: float):
        """Set the channel reach length computed from the reach/xs/ds_xs geometry."""
        self.computed_channel_reach_length = self.computed_river_station - computed_river_station

    def set_computed_reach_length_ratio(self):
        """Set the ratio of the computed channel reach length to the model channel reach length."""
        self.computed_channel_reach_length_ratio = self.computed_channel_reach_length / self.channel_reach_length

    @cached_property
    def computed_river_station(self):
        """The computed river stationing according to the reach geometry."""
        return reverse(self.reach_geom).project(self.centerline_intersection_point)

    @cached_property
    def centerline_intersection_point(self):
        """A point located where the cross section and reach centerline intersect."""
        if self.cross_section_intersects_reach:
            intersection = self.reach_geom.intersection(self.geom)
            if intersection.geom_type == "Point":
                return intersection
            if intersection.geom_type == "MultiPoint":
                return intersection.geoms[0]

    @cached_property
    def cross_section_intersects_reach(self):
        """Detemine if the cross section intersects the reach, if not return False, otherwise return True."""
        return self.reach_geom.intersects(self.geom)

    @cached_property
    def left_reach_length_ratio(self):
        """The ratio of the left reach length to the channel reach length."""
        if self.reach_lengths_populated:
            return self.left_reach_length / self.channel_reach_length

    @cached_property
    def right_reach_length_ratio(self):
        """The ratio of the right reach length to the channel reach length."""
        if self.reach_lengths_populated:
            return self.right_reach_length / self.channel_reach_length

    @cached_property
    def reach_lengths_populated(self):
        """A boolean indicating if all the reach lengths are poputed."""
        if np.isnan(self.reach_lengths).any():
            return False
        elif len([i for i in self.reach_lengths if i == 0]) > 0:
            return False
        else:
            return True

    @cached_property
    def reach_lengths(self):
        """The reach lengths of the cross section."""
        return [self.right_reach_length, self.left_reach_length, self.channel_reach_length]

    @cached_property
    def left_bank_station(self):
        """The cross sections left bank station."""
        return float(self.bank_stations[0])

    @cached_property
    def right_bank_station(self):
        """The cross sections right bank station."""
        return float(self.bank_stations[1])

    @cached_property
    def left_bank_elevation(self):
        """Elevation of the left bank station."""
        return self.station_elevation_df.loc[
            self.station_elevation_df["Station"] == self.left_bank_station, "Elevation"
        ].iloc[0]

    @cached_property
    def right_bank_elevation(self):
        """Elevation of the right bank station."""
        return self.station_elevation_df.loc[
            self.station_elevation_df["Station"] == self.right_bank_station, "Elevation"
        ].iloc[0]

    @cached_property
    def station_elevation_df(self):
        """A pandas DataFrame containing the station-elevation data of the cross section."""
        return pd.DataFrame(self.station_elevation_points, columns=["Station", "Elevation"])

    @cached_property
    def skew(self):
        """The skew applied to the cross section."""
        skew = search_contents(self.ras_data, "Skew Angle", expect_one=False, require_one=False)
        if len(skew) == 1:
            return float(skew[0])
        elif len(skew) > 1:
            raise ValueError(
                f"Expected only one skew value for the cross section recieved: {len(skew)}. XS: {self.river_reach_rs}"
            )

    @cached_property
    def max_n(self):
        """The highest manning's n value used in the cross section."""
        return max(list(zip(*self.mannings))[1])

    @cached_property
    def min_n(self):
        """The lowest manning's n value used in the cross section."""
        return min(list(zip(*self.mannings))[1])

    @cached_property
    def mannings(self):
        """The manning's values of the cross section."""
        try:
            lines = text_block_from_start_str_length(
                "#Mann=" + search_contents(self.ras_data, "#Mann", expect_one=True, require_one=False),
                math.ceil(self.number_of_mannings_points / 4),
                self.ras_data,
            )
            return data_triplets_from_text_block(lines, 24)
        except ValueError as e:
            print(e)
            return None

    @cached_property
    def number_of_mannings_points(self):
        """The number of mannings points in the cross section."""
        return int(search_contents(self.ras_data, "#Mann", expect_one=True).split(",")[0])

    @cached_property
    def has_ineffectives(self):
        """A boolean indicating if the cross section contains ineffective flow areas."""
        ineff = search_contents(self.ras_data, "#XS Ineff", expect_one=False, require_one=False)
        if len(ineff) > 0:
            return True
        else:
            return False

    @cached_property
    def has_levees(self):
        """A boolean indicating if the cross section contains levees."""
        levees = search_contents(self.ras_data, "Levee", expect_one=False, require_one=False)
        if len(levees) > 0:
            return True
        else:
            return False

    @cached_property
    def has_blocks(self):
        """A boolean indicating if the cross section contains blocked obstructions."""
        blocks = search_contents(self.ras_data, "#Block Obstruct", expect_one=False, require_one=False)
        if len(blocks) > 0:
            return True
        else:
            return False

    @cached_property
    def channel_obstruction(self):
        """A boolean indicating if the channel is being blocked.

        A boolean indicating if ineffective flow area, blocked obstructions, or levees are contained
        in the channel (between bank stations).
        """
        return None  # TODO: This feature was never added in ripple1d

    def set_thalweg_drop(self, ds_thalweg):
        """Set the drop in thalweg elevation between this cross section and the downstream cross section."""
        self.thalweg_drop = self.thalweg - ds_thalweg

    @cached_property
    def left_max_elevation(self):
        """Max Elevation on the left side of the channel."""
        return self.station_elevation_df.loc[
            self.station_elevation_df["Station"] <= self.left_bank_station, "Elevation"
        ].max()

    @cached_property
    def right_max_elevation(self):
        """Max Elevation on the right side of the channel."""
        df = pd.DataFrame(self.station_elevation_points, columns=["Station", "Elevation"])
        return df.loc[df["Station"] >= self.right_bank_station, "Elevation"].max()

    @cached_property
    def overtop_elevation(self):
        """The elevation to at which the cross secition will be overtopped."""
        return min(self.right_max_elevation, self.left_max_elevation)

    @cached_property
    def channel_width(self):
        """The width of the cross section between bank points."""
        return self.right_bank_station - self.left_bank_station

    @cached_property
    def channel_depth(self):
        """The depth of the channel; i.e., the depth at which the first bank station is overtoppped."""
        return min([self.left_bank_elevation, self.right_bank_elevation]) - self.thalweg

    @cached_property
    def station_elevation_point_density(self):
        """The average spacing of the station-elevation points."""
        return self.cutline_length / self.number_of_station_elevation_points

    @cached_property
    def cutline_length(self):
        """Length of the cross section bassed on the geometry (x-y coordinates)."""
        return self.geom.length

    @cached_property
    def htab_min_elevation(self):
        """The starting elevation for the cross section's htab."""
        result = search_contents(self.ras_data, "XS HTab Starting El and Incr", expect_one=False, require_one=False)
        if len(result) == 1:
            return result[0].split(",")[0]

    @cached_property
    def htab_min_increment(self):
        """The increment for the cross section's htab."""
        result = search_contents(self.ras_data, "XS HTab Starting El and Incr", expect_one=False, require_one=False)
        if len(result) == 1:
            return result[0].split(",")[1]

    @cached_property
    def htab_points(self):
        """The number of points on the cross section's htab."""
        result = search_contents(self.ras_data, "XS HTab Starting El and Incr", expect_one=False, require_one=False)
        if len(result) == 1:
            return result[0].split(",")[2]

    @cached_property
    def correct_cross_section_direction(self):
        """A boolean indicating if the cross section is drawn from right to left looking downstream."""
        if self.cross_section_intersects_reach:
            offset = self.geom.offset_curve(-1)
            if self.reach_geom.intersects(offset):  # if the offset line intersects then use this logic
                point = self.reach_geom.intersection(offset)
                point = validate_point(point)

                offset_rs = reverse(self.reach_geom).project(point)
                if self.computed_river_station < offset_rs:
                    return True
                else:
                    return False
            else:  # if the original offset line did not intersect then try offsetting the other direction and applying
                # the opposite stationing logic; the orginal line may have gone beyound the other line.
                offset = self.geom.offset_curve(1)
                point = self.reach_geom.intersection(offset)
                point = validate_point(point)

                offset_rs = reverse(self.reach_geom).project(point)
                if self.computed_river_station > offset_rs:
                    return True
                else:
                    return False
        else:
            return False

    @cached_property
    def horizontal_varying_mannings(self):
        """A boolean indicating if horizontally varied mannings values are applied."""
        if self.mannings_code == -1:
            return True
        elif self.mannings_code == 0:
            return False
        else:
            return False

    @cached_property
    def mannings_code(self):
        """A code indicating what type of manning's values are used.

        0, -1 correspond to 3 value manning's; horizontally varying manning's values, respectively.
        """
        return int(search_contents(self.ras_data, "#Mann", expect_one=True).split(",")[1])

    @cached_property
    def expansion_coefficient(self):
        """The expansion coefficient for the cross section."""
        return search_contents(self.ras_data, "Exp/Cntr", expect_one=True).split(",")[0]

    @cached_property
    def contraction_coefficient(self):
        """The expansion coefficient for the cross section."""
        return search_contents(self.ras_data, "Exp/Cntr", expect_one=True).split(",")[1]

    @cached_property
    def centerline_intersection_station(self):
        """Station along the cross section where the centerline intersects it."""
        if self.cross_section_intersects_reach:
            return self.geom.project(self.centerline_intersection_point)

    def set_bridge_xs(self, br: int):
        """Set the bridge cross section attribute.

        A value of 0 is added for non-bridge cross sections and 4, 3, 2, 1 are
        set for each of the bridge cross sections from downstream to upstream order.
        """
        self.bridge_xs = br

    @cached_property
    def intersects_reach_once(self):
        """A boolean indicating if the cross section intersects the reach only once."""
        if isinstance(self.centerline_intersection_point, LineString):
            return False
        elif self.centerline_intersection_point is None:
            return False
        elif isinstance(self.centerline_intersection_point, Point):
            return True
        else:
            raise TypeError(
                f"Unexpected type resulting from intersecting cross section and reach; expected Point or LineString; recieved: {type(self.centerline_intersection_point)}. {self.river_reach_rs}"
            )

    @cached_property
    def gdf_data_dict(self):
        """Cross section geodataframe."""
        return {
            "geometry": self.geom,
            "river": self.river,
            "reach": self.reach,
            "river_reach": self.river_reach,
            "river_station": self.river_station,
            "river_reach_rs": self.river_reach_rs,
            "river_reach_rs_str": self.river_reach_rs_str,
            "thalweg": self.thalweg,
            "xs_max_elevation": self.xs_max_elevation,
            "left_reach_length": self.left_reach_length,
            "right_reach_length": self.right_reach_length,
            "channel_reach_length": self.channel_reach_length,
            "computed_channel_reach_length": self.computed_channel_reach_length,
            "computed_channel_reach_length_ratio": self.computed_channel_reach_length_ratio,
            "left_reach_length_ratio": self.left_reach_length_ratio,
            "right_reach_length_ratio": self.right_reach_length_ratio,
            "reach_lengths_populated": self.reach_lengths_populated,
            "ras_data": "\n".join(self.ras_data),
            "station_elevation_points": self.station_elevation_points,
            "bank_stations": self.bank_stations,
            "left_bank_station": self.left_bank_station,
            "right_bank_station": self.right_bank_station,
            "left_bank_elevation": self.left_bank_elevation,
            "right_bank_elevation": self.right_bank_elevation,
            "number_of_station_elevation_points": self.number_of_station_elevation_points,
            "number_of_coords": self.number_of_coords,
            "station_length": self.station_length,
            "cutline_length": self.geom.length,
            "xs_length_ratio": self.xs_length_ratio,
            "banks_encompass_channel": self.banks_encompass_channel,
            "skew": self.skew,
            "max_n": self.max_n,
            "min_n": self.min_n,
            "has_lateral_structure": self.has_lateral_structures,
            "has_ineffective": self.has_ineffectives,
            "has_levees": self.has_levees,
            "has_blocks": self.has_blocks,
            "channel_obstruction": self.channel_obstruction,
            "thalweg_drop": self.thalweg_drop,
            "left_max_elevation": self.left_max_elevation,
            "right_max_elevation": self.right_max_elevation,
            "overtop_elevation": self.overtop_elevation,
            "min_elevation": self.min_elevation,
            "channel_width": self.channel_width,
            "channel_depth": self.channel_depth,
            "station_elevation_point_density": self.station_elevation_point_density,
            "htab_min_elevation": self.htab_min_elevation,
            "htab_min_increment": self.htab_min_increment,
            "htab points": self.htab_points,
            "correct_cross_section_direction": self.correct_cross_section_direction,
            "horizontal_varying_mannings": self.horizontal_varying_mannings,
            "number_of_mannings_points": self.number_of_mannings_points,
            "expansion_coefficient": self.expansion_coefficient,
            "contraction_coefficient": self.contraction_coefficient,
            "centerline_intersection_station": self.centerline_intersection_station,
            "bridge_xs": self.bridge_xs,
            "cross_section_intersects_reach": self.cross_section_intersects_reach,
            "intersects_reach_once": self.intersects_reach_once,
            "min_elevation_in_channel": self.min_elevation_in_channel,
        }

    @cached_property
    def gdf(self):
        """Cross section geodataframe."""
        return gpd.GeoDataFrame(self.gdf_data_dict, geometry="geometry")

    @cached_property
    def n_subdivisions(self) -> int:
        """Get the number of subdivisions (defined by manning's n)."""
        return int(search_contents(self.ras_data, "#Mann", expect_one=True).split(",")[0])

    @cached_property
    def subdivision_type(self) -> int:
        """Get the subdivision type.

        -1 seems to indicate horizontally-varied n.  0 seems to indicate subdivisions by LOB, channel, ROB.
        """
        return int(search_contents(self.ras_data, "#Mann", expect_one=True).split(",")[1])

    @cached_property
    def subdivisions(self) -> Optional[tuple[list[float], list[float]]]:
        """Get the stations corresponding to subdivision breaks, along with their roughness."""
        try:
            header = [l for l in self.ras_data if l.startswith("#Mann")][0]
            lines = text_block_from_start_str_length(
                header,
                math.ceil(self.n_subdivisions / 3),
                self.ras_data,
            )

            return delimited_pairs_to_lists(lines)
        except ValueError:
            return None

    @cached_property
    def is_interpolated(self) -> bool:
        """Check if xs is interpolated."""
        if self._is_interpolated == None:
            self._is_interpolated = "*" in self.split_xs_header(1)
        return self._is_interpolated

    def wse_intersection_pts(self, wse: float) -> list[tuple[float, float]]:
        """Find where the cross-section terrain intersects the water-surface elevation."""
        section_pts = self.station_elevation_points
        intersection_pts = []

        # Iterate through all pairs of points and find any points where the line would cross the wse
        for i in range(len(section_pts) - 1):
            p1 = section_pts[i]
            p2 = section_pts[i + 1]

            if (p1[1] > wse and p2[1] > wse) or (p1[1] < wse and p2[1] < wse):
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
        return intersection_pts

    def get_wetted_perimeter(self, wse: float, start: float = None, stop: float = None) -> float:
        """Get the hydraulic radius of the cross-section at a given WSE."""
        df = pd.DataFrame(self.station_elevation_points, columns=["x", "y"])
        df = pd.concat([df, pd.DataFrame(self.wse_intersection_pts(wse), columns=["x", "y"])])
        if start is not None:
            df = df[df["x"] >= start]
        if stop is not None:
            df = df[df["x"] <= stop]
        df = df.sort_values("x", ascending=True)
        df = df[df["y"] <= wse]
        if len(df) == 0:
            return 0
        df["dx"] = df["x"].diff(-1)
        df["dy"] = df["y"].diff(-1)
        df["d"] = ((df["x"] ** 2) + (df["y"] ** 2)) ** (0.5)

        return df["d"].cumsum().values[0]

    def get_flow_area(self, wse: float, start: float = None, stop: float = None) -> float:
        """Get the flow area of the cross-section at a given WSE."""
        df = pd.DataFrame(self.station_elevation_points, columns=["x", "y"])
        df = pd.concat([df, pd.DataFrame(self.wse_intersection_pts(wse), columns=["x", "y"])])
        if start is not None:
            df = df[df["x"] >= start]
        if stop is not None:
            df = df[df["x"] <= stop]
        df = df.sort_values("x", ascending=True)
        df = df[df["y"] <= wse]
        if len(df) == 0:
            return 0
        df["d"] = wse - df["y"]  # depth
        df["d2"] = df["d"].shift(-1)
        df["x2"] = df["x"].shift(-1)
        df["a"] = ((df["d"] + df["d2"]) / 2) * (df["x2"] - df["x"])  # area of a trapezoid

        return df["a"].cumsum().values[0]

    def get_mannings_discharge(self, wse: float, slope: float, units: str) -> float:
        """Calculate the discharge of the cross-section according to manning's equation."""
        q = 0
        stations, mannings = self.subdivisions
        slope = slope**0.5  # pre-process slope for efficiency
        for i in range(self.n_subdivisions - 1):
            start = stations[i]
            stop = stations[i + 1]
            n = mannings[i]
            area = self.get_flow_area(wse, start, stop)
            if area == 0:
                continue
            perimeter = self.get_wetted_perimeter(wse, start, stop)
            rh = area / perimeter
            tmp_q = (1 / n) * area * (rh ** (2 / 3)) * slope
            if units == "english":
                tmp_q *= 1.49
            q += (1 / n) * area * (rh ** (2 / 3)) * slope
        return q


class StructureType(Enum):
    """Structure types."""

    XS = 1
    CULVERT = 2
    BRIDGE = 3
    MULTIPLE_OPENING = 4
    INLINE_STRUCTURE = 5
    LATERAL_STRUCTURE = 6


class Structure:
    """HEC-RAS Structures."""

    def __init__(
        self,
        ras_data: list[str],
        river_reach: str,
        river: str,
        reach: str,
        us_xs: XS,
    ):
        self.ras_data = ras_data
        self.river = river
        self.reach = reach
        self.river_reach = river_reach
        self.river_reach_rs = f"{river} {reach} {self.river_station}"
        self.us_xs = us_xs

    def split_structure_header(self, position: int) -> str:
        """Split Structure header.

        Example: Type RM Length L Ch R = 3 ,83554.  ,237.02,192.39,113.07.
        """
        header = search_contents(self.ras_data, "Type RM Length L Ch R ", expect_one=True)

        return header.split(",")[position]

    @cached_property
    def river_station(self) -> float:
        """Structure river station."""
        return float(self.split_structure_header(1))

    @cached_property
    def type_int(self) -> int:
        """Structure type."""
        return int(self.split_structure_header(0))

    @cached_property
    def type(self) -> StructureType:
        """Structure type."""
        return StructureType(self.type_int)

    def structure_data(self, position: int) -> str | int:
        """Structure data."""
        if self.type in [
            StructureType.XS,
            StructureType.CULVERT,
            StructureType.BRIDGE,
            StructureType.MULTIPLE_OPENING,
        ]:  # 1 = Cross Section, 2 = Culvert, 3 = Bridge, 4 = Multiple Opening
            data = text_block_from_start_str_length(
                "Deck Dist Width WeirC Skew NumUp NumDn MinLoCord MaxHiCord MaxSubmerge Is_Ogee",
                1,
                self.ras_data,
            )
            return data[0].split(",")[position]
        elif self.type == StructureType.INLINE_STRUCTURE:  # 5 = Inline Structure
            data = text_block_from_start_str_length(
                "IW Dist,WD,Coef,Skew,MaxSub,Min_El,Is_Ogee,SpillHt,DesHd",
                1,
                self.ras_data,
            )
            return data[0].split(",")[position]
        elif self.type == StructureType.LATERAL_STRUCTURE:  # 6 = Lateral Structure
            return 0

    @cached_property
    def distance(self) -> float:
        """Distance to upstream cross section."""
        return float(self.structure_data(0))

    @cached_property
    def width(self) -> float:
        """Structure width."""
        # TODO check units of the RAS model
        return float(self.structure_data(1))

    @cached_property
    def gdf(self) -> gpd.GeoDataFrame:
        """Structure geodataframe."""
        return gpd.GeoDataFrame(
            {
                "geometry": [LineString(self.us_xs.coords).offset_curve(self.distance)],
                "river": [self.river],
                "reach": [self.reach],
                "river_reach": [self.river_reach],
                "river_station": [self.river_station],
                "river_reach_rs": [self.river_reach_rs],
                "type": [self.type_int],
                "distance": [self.distance],
                "width": [self.width],
                "ras_data": ["\n".join(self.ras_data)],
            },
            geometry="geometry",
        )

    @cached_property
    def distance_to_us_xs(self):
        """The distance from the upstream cross section to the start of the lateral structure."""
        try:
            return float(search_contents(self.ras_data, "Lateral Weir Distance", expect_one=True))
        except ValueError:
            raise InvalidStructureDataError(
                f"The weir distance for the lateral structure is not populated for: {self.river},{self.reach},{self.river_station}"
            )

    @cached_property
    def weir_length(self):
        """The length weir."""
        if self.type == StructureType.LATERAL_STRUCTURE:
            try:
                return float(list(zip(*self.station_elevation_points))[0][-1])
            except IndexError:
                raise InvalidStructureDataError(
                    f"No station elevation data for: {self.river}, {self.reach}, {self.river_station}"
                )

    @cached_property
    def station_elevation_points(self):
        """Station elevation points."""
        try:
            lines = text_block_from_start_str_length(
                f"Lateral Weir SE= {self.number_of_station_elevation_points} ",
                math.ceil(self.number_of_station_elevation_points / 5),
                self.ras_data,
            )
            return data_pairs_from_text_block(lines, 16)
        except ValueError:
            return None

    @cached_property
    def tail_water_river(self):
        """The tail water reache's river name."""
        return search_contents(self.ras_data, "Lateral Weir End", expect_one=True).split(",")[0].rstrip()

    @cached_property
    def tail_water_reach(self):
        """The tail water reache's reach name."""
        return search_contents(self.ras_data, "Lateral Weir End", expect_one=True).split(",")[1].rstrip()

    @cached_property
    def tail_water_river_station(self):
        """The tail water reache's river stationing."""
        return float(search_contents(self.ras_data, "Lateral Weir End", expect_one=True).split(",")[2])

    @cached_property
    def tw_distance(self):
        """The distance between the tail water upstream cross section and the lateral weir."""
        return float(
            search_contents(self.ras_data, "Lateral Weir Connection Pos and Dist", expect_one=True).split(",")[1]
        )

    @cached_property
    def number_of_station_elevation_points(self):
        """The number of station elevation points."""
        return int(search_contents(self.ras_data, "Lateral Weir SE", expect_one=True))


class Reach:
    """HEC-RAS River Reach."""

    def __init__(self, ras_data: list[str], river_reach: str):
        reach_lines = text_block_from_start_end_str(f"River Reach={river_reach}", ["River Reach"], ras_data, -1)
        self.ras_data = reach_lines
        self.river_reach = river_reach
        self.river = river_reach.split(",")[0].rstrip()
        self.reach = river_reach.split(",")[1].rstrip()

    @cached_property
    def us_xs(self) -> "XS":
        """Upstream cross section."""
        return self.cross_sections[
            self.xs_gdf.loc[
                self.xs_gdf["river_station"] == self.xs_gdf["river_station"].max(),
                "river_reach_rs",
            ][0]
        ]

    @cached_property
    def ds_xs(self) -> "XS":
        """Downstream cross section."""
        return self.cross_sections[
            self.xs_gdf.loc[
                self.xs_gdf["river_station"] == self.xs_gdf["river_station"].min(),
                "river_reach_rs",
            ][0]
        ]

    @cached_property
    def number_of_cross_sections(self) -> int:
        """Number of cross sections."""
        return len(self.cross_sections)

    @cached_property
    def number_of_coords(self) -> int:
        """Number of coordinates in reach."""
        return int(search_contents(self.ras_data, "Reach XY"))

    @cached_property
    def coords(self) -> list[tuple[float, float]]:
        """Reach coordinates."""
        lines = text_block_from_start_str_length(
            f"Reach XY= {self.number_of_coords} ",
            math.ceil(self.number_of_coords / 2),
            self.ras_data,
        )
        return data_pairs_from_text_block(lines, 32)

    @cached_property
    def reach_nodes(self) -> list[str]:
        """Reach nodes."""
        return search_contents(self.ras_data, "Type RM Length L Ch R ", expect_one=False, require_one=False)

    @cached_property
    def cross_sections(self):
        """Cross sections."""
        cross_sections = OrderedDict()
        bridge_xs = []
        for header in self.reach_nodes:
            xs_type, _, _, _, _ = header.split(",")[:5]

            # Identify bridge cross-sections
            if int(xs_type) in [2, 3, 4]:
                bridge_xs = bridge_xs[:-2] + [4, 3]  # Update after discovery
            if int(xs_type) != 1:
                continue
            if len(bridge_xs) == 0:
                bridge_xs = [0]  # Initialize list
            else:
                bridge_xs.append(max([0, bridge_xs[-1] - 1]))  # Autodecrement XS number until zero

            # Get xs text
            xs_lines = text_block_from_start_end_str(
                f"Type RM Length L Ch R ={header}",
                ["Type RM Length L Ch R", "River Reach"],
                self.ras_data,
            )
            cross_section = XS(xs_lines, self.river_reach, self.river, self.reach, self.geom)
            cross_sections[cross_section.river_reach_rs] = cross_section

        for i, br in zip(cross_sections, bridge_xs):
            cross_sections[i].set_bridge_xs(br)
        if len(cross_sections) > 1:
            cross_sections = self.compute_multi_xs_variables(cross_sections)

        return dict(cross_sections)  # Cast to regular dict

    @cached_property
    def structures(self) -> dict[str, "Structure"]:
        """Structures."""
        structures = {}
        for header in self.reach_nodes:
            xs_type, _, _, _, _ = header.split(",")[:5]
            if int(xs_type) == 1:
                xs_lines = text_block_from_start_end_str(
                    f"Type RM Length L Ch R ={header}",
                    ["Type RM Length L Ch R", "River Reach"],
                    self.ras_data,
                )
                cross_section = XS(xs_lines, self.river_reach, self.river, self.reach)
                continue
            elif int(xs_type) in [2, 3, 4, 5, 6]:  # culvert or bridge or multiple openeing
                structure_lines = text_block_from_start_end_str(
                    f"Type RM Length L Ch R ={header}",
                    ["Type RM Length L Ch R", "River Reach"],
                    self.ras_data,
                )
            else:
                raise TypeError(
                    f"Unsupported structure type: {int(xs_type)}. Supported structure types are 2, 3, 4, 5, and 6 corresponding to culvert, \
                        bridge, multiple openeing, inline structure, lateral structure, respectively"
                )

            structure = Structure(
                structure_lines,
                self.river_reach,
                self.river,
                self.reach,
                cross_section,
            )
            structures[structure.river_reach_rs] = structure

        return structures

    @cached_property
    def gdf(self) -> gpd.GeoDataFrame:
        """Reach geodataframe."""
        return gpd.GeoDataFrame(
            {
                "geometry": [LineString(self.coords)],
                "river": [self.river],
                "reach": [self.reach],
                "river_reach": [self.river_reach],
                # "number_of_coords": [self.number_of_coords],
                # "coords": [self.coords],
                "ras_data": ["\n".join(self.ras_data)],
            },
            geometry="geometry",
        )

    @cached_property
    def xs_gdf(self) -> gpd.GeoDataFrame:
        """Cross section geodataframe."""
        gdfs = [xs.gdf for xs in self.cross_sections.values()]
        if len(gdfs) > 0:
            return pd.concat(gdfs)
        else:
            return gpd.GeoDataFrame()

    @cached_property
    def structures_gdf(self) -> gpd.GeoDataFrame:
        """Structures geodataframe."""
        gdfs = [structure.gdf for structure in self.structures.values()]
        if len(gdfs) > 0:
            return pd.concat(gdfs)
        else:
            return gpd.GeoDataFrame()

    def compute_multi_xs_variables(self, cross_sections: OrderedDict) -> dict:
        """Compute variables that depend on multiple cross sections.

        Set the thalweg drop, computed channel reach length and computed channel reach length
        ratio between a cross section and the cross section downstream.
        """
        keys = list(cross_sections.keys())
        last_xs = cross_sections[keys[-1]]
        for xs in keys[::-1][1:]:
            cross_sections[xs].set_thalweg_drop(last_xs.thalweg)
            cross_sections[xs].set_computed_reach_length(last_xs.computed_river_station)
            cross_sections[xs].set_computed_reach_length_ratio()
            last_xs = cross_sections[xs]
        return cross_sections

    @cached_property
    def geom(self):
        """Geometry of the reach."""
        return LineString(self.coords)


class Junction:
    """HEC-RAS Junction."""

    def __init__(self, ras_data: list[str], junct: str):
        self.name = junct
        self.ras_data = text_block_from_start_str_to_empty_line(f"Junct Name={junct}", ras_data)

    def split_lines(self, lines: list[str], token: str, idx: int) -> list[str]:
        """Split lines."""
        return [line.split(token)[idx].rstrip() for line in lines]

    @property
    def x(self) -> float:
        """Junction x coordinate."""
        return float(self.split_lines([search_contents(self.ras_data, "Junct X Y & Text X Y")], ",", 0)[0])

    @property
    def y(self):
        """Junction y coordinate."""
        return float(self.split_lines([search_contents(self.ras_data, "Junct X Y & Text X Y")], ",", 1)[0])

    @property
    def point(self) -> Point:
        """Junction point."""
        return Point(self.x, self.y)

    @property
    def upstream_rivers(self) -> str:
        """Upstream rivers."""
        return ",".join(
            self.split_lines(
                search_contents(self.ras_data, "Up River,Reach", expect_one=False),
                ",",
                0,
            )
        )

    @property
    def downstream_rivers(self) -> str:
        """Downstream rivers."""
        return ",".join(
            self.split_lines(
                search_contents(self.ras_data, "Dn River,Reach", expect_one=False),
                ",",
                0,
            )
        )

    @property
    def upstream_reaches(self) -> str:
        """Upstream reaches."""
        return ",".join(
            self.split_lines(
                search_contents(self.ras_data, "Up River,Reach", expect_one=False),
                ",",
                1,
            )
        )

    @property
    def downstream_reaches(self) -> str:
        """Downstream reaches."""
        return ",".join(
            self.split_lines(
                search_contents(self.ras_data, "Dn River,Reach", expect_one=False),
                ",",
                1,
            )
        )

    @property
    def junction_lengths(self) -> str:
        """Junction lengths."""
        return ",".join(self.split_lines(search_contents(self.ras_data, "Junc L&A", expect_one=False), ",", 0))

    @property
    def gdf(self):
        """Junction geodataframe."""
        return gpd.GeoDataFrame(
            {
                "geometry": [self.point],
                "junction_lengths": [self.junction_lengths],
                "us_rivers": [self.upstream_rivers],
                "ds_rivers": [self.downstream_rivers],
                "us_reaches": [self.upstream_reaches],
                "ds_reaches": [self.downstream_reaches],
                "ras_data": ["\n".join(self.ras_data)],
            },
            geometry="geometry",
        )


class StorageArea:
    """HEC-RAS StorageArea."""

    def __init__(self, ras_data: list[str]):
        self.ras_data = ras_data
        # TODO: Implement this


class Connection:
    """HEC-RAS Connection."""

    def __init__(self, ras_data: list[str]):
        self.ras_data = ras_data
        # TODO: Implement this


class ProjectFile(CachedFile):
    """HEC-RAS Project file."""

    @cached_property
    def project_title(self) -> str:
        """Return the project title."""
        return search_contents(self.file_lines, "Proj Title")

    @cached_property
    def project_description(self) -> str:
        """Return the model description."""
        return search_contents(self.file_lines, "Model Description", token=":", require_one=False)

    @cached_property
    def project_status(self) -> str:
        """Return the model status."""
        return search_contents(self.file_lines, "Status of Model", token=":", require_one=False)

    @cached_property
    def project_units(self) -> str | None:
        """Return the project units."""
        for line in self.file_lines:
            if "Units" in line:
                return " ".join(line.split(" ")[:-1])

    @cached_property
    def plan_current(self) -> str | None:
        """Return the current plan."""
        try:
            suffix = search_contents(self.file_lines, "Current Plan", expect_one=True, require_one=False).strip()
            return name_from_suffix(self.fpath, suffix)
        except Exception:
            return None

    @cached_property
    def ras_version(self) -> str | None:
        """Return the ras version."""
        version = search_contents(self.file_lines, "Program Version", token="=", expect_one=False, require_one=False)
        if version == []:
            version = search_contents(
                self.file_lines, "Program and Version", token=":", expect_one=False, require_one=False
            )
        if version == []:
            return "N/A"
        else:
            return version[0]

    @cached_property
    def plan_files(self) -> list[str]:
        """Return the plan files."""
        suffixes = search_contents(self.file_lines, "Plan File", expect_one=False, require_one=False)
        return [name_from_suffix(self.fpath, i) for i in suffixes]

    @cached_property
    def geometry_files(self) -> list[str]:
        """Return the geometry files."""
        suffixes = search_contents(self.file_lines, "Geom File", expect_one=False, require_one=False)
        return [name_from_suffix(self.fpath, i) for i in suffixes]

    @cached_property
    def steady_flow_files(self) -> list[str]:
        """Return the flow files."""
        suffixes = search_contents(self.file_lines, "Flow File", expect_one=False, require_one=False)
        return [name_from_suffix(self.fpath, i) for i in suffixes]

    @cached_property
    def quasi_unsteady_flow_files(self) -> list[str]:
        """Return the quasisteady flow files."""
        suffixes = search_contents(self.file_lines, "QuasiSteady File", expect_one=False, require_one=False)
        return [name_from_suffix(self.fpath, i) for i in suffixes]

    @cached_property
    def unsteady_flow_files(self) -> list[str]:
        """Return the unsteady flow files."""
        suffixes = search_contents(self.file_lines, "Unsteady File", expect_one=False, require_one=False)
        return [name_from_suffix(self.fpath, i) for i in suffixes]


class PlanFile(CachedFile):
    """HEC-RAS Plan file asset."""

    @cached_property
    def plan_title(self) -> str:
        """Return plan title."""
        return search_contents(self.file_lines, "Plan Title", require_one=False)

    @cached_property
    def plan_version(self) -> str:
        """Return program version."""
        return search_contents(self.file_lines, "Program Version", require_one=False)

    @cached_property
    def geometry_file(self) -> str:
        """Return geometry file."""
        suffix = search_contents(self.file_lines, "Geom File", expect_one=True)
        return name_from_suffix(self.fpath, suffix)

    @cached_property
    def flow_file(self) -> str:
        """Return flow file."""
        suffix = search_contents(self.file_lines, "Flow File", expect_one=True)
        return name_from_suffix(self.fpath, suffix)

    @cached_property
    def short_identifier(self) -> str:
        """Return short identifier."""
        si = search_contents(self.file_lines, "Short Identifier", expect_one=True, require_one=False)
        if len(si) == 1:
            return si.strip()

    @cached_property
    def is_encroached(self) -> bool:
        """Check if any nodes are encroached."""
        return any(["Encroach Node" in i for i in self.file_lines])

    @cached_property
    def breach_locations(self) -> dict:
        """Return breach locations.

        Example file line:
        Breach Loc=                ,                ,        ,True,HH_DamEmbankment
        """
        breach_dict = {}
        matches = search_contents(self.file_lines, "Breach Loc", expect_one=False, require_one=False)
        for line in matches:
            parts = line.split(",")
            if len(parts) >= 4:
                key = parts[4].strip()
                breach_dict[key] = eval(parts[3].strip())
        return breach_dict


class GeometryFile(CachedFile):
    """HEC-RAS Geometry file asset."""

    @cached_property
    def geom_title(self) -> str:
        """Return geometry title."""
        return search_contents(self.file_lines, "Geom Title")

    @cached_property
    def geom_version(self) -> str:
        """Return program version."""
        v = search_contents(self.file_lines, "Program Version", require_one=False)
        if len(v) == 0:
            return "N/A"
        else:
            return v

    @cached_property
    def file_version(self) -> str:
        """Provide consistent syntax with RasHDFFile."""
        return self.geom_version

    @cached_property
    def geometry_time(self) -> list[datetime.datetime]:
        """Get the latest node last updated entry for this geometry."""
        dts = search_contents(self.file_lines, "Node Last Edited Time", expect_one=False, require_one=False)
        if len(dts) >= 1:
            try:
                return [datetime.datetime.strptime(d, "%b/%d/%Y %H:%M:%S") for d in dts]
            except ValueError:
                return []
        else:
            return []

    @cached_property
    def has_2d(self) -> bool:
        """Check if RAS geometry has any 2D areas."""
        for line in self.file_lines:
            if line.startswith("Storage Area Is2D=") and int(line[len("Storage Area Is2D=") :].strip()) in (1, -1):
                # RAS mostly uses "-1" to indicate True and "0" to indicate False. Checking for "1" also here.
                return True
        return False

    @cached_property
    def has_1d(self) -> bool:
        """Check if RAS geometry has any 1D components."""
        return len(self.cross_sections) > 0

    @cached_property
    def rivers(self) -> dict[str, River]:
        """A dictionary of river_name: River (class) for the rivers contained in the HEC-RAS geometry file."""
        tmp_rivers = defaultdict(list)
        for reach in self.reaches.values():  # First, group all reaches into their respective rivers
            tmp_rivers[reach.river].append(reach.reach)
        for (
            river,
            reaches,
        ) in tmp_rivers.items():  # Then, create a River object for each river
            tmp_rivers[river] = River(river, reaches)
        return tmp_rivers

    @cached_property
    def reaches(self) -> dict[str, Reach]:
        """A dictionary of the reaches contained in the HEC-RAS geometry file."""
        reg = r"^\s*River Reach=.*"
        river_reaches = search_contents(self.file_lines, reg, expect_one=False, require_one=False, regex=True)
        return {river_reach: Reach(self.file_lines, river_reach) for river_reach in river_reaches}

    @cached_property
    def junctions(self) -> dict[str, Junction]:
        """A dictionary of the junctions contained in the HEC-RAS geometry file."""
        juncts = search_contents(self.file_lines, "Junct Name", expect_one=False, require_one=False)
        return {junction: Junction(self.file_lines, junction) for junction in juncts}

    @cached_property
    def cross_sections(self) -> dict[str, XS]:
        """A dictionary of all the cross sections contained in the HEC-RAS geometry file."""
        cross_sections = {}
        for reach in self.reaches.values():
            cross_sections.update(reach.cross_sections)
        return cross_sections

    @cached_property
    def structures(self) -> dict[str, Structure]:
        """A dictionary of the structures contained in the HEC-RAS geometry file."""
        structures = {}
        for reach in self.reaches.values():
            structures.update(reach.structures)
        return structures

    @cached_property
    def storage_areas(self) -> dict[str, StorageArea]:
        """A dictionary of the storage areas contained in the HEC-RAS geometry file."""
        matches = search_contents(self.file_lines, "Storage Area", expect_one=False, require_one=False)
        areas = []
        for line in matches:
            if "," in line:
                parts = line.split(",")
                areas.append(parts[0].strip())
            else:
                areas.append(line.strip())
        return {a: StorageArea(a) for a in areas}

    @cached_property
    def ic_point_names(self) -> list[str]:
        """A list of the initial condition point names contained in the HEC-RAS geometry file."""
        ic_points = search_contents(self.file_lines, "IC Point Name", expect_one=False, require_one=False)
        return [ic_point.strip() for ic_point in ic_points]

    @cached_property
    def ref_line_names(self) -> list[str]:
        """A list of reference line names contained in the HEC-RAS geometry file."""
        ref_lines = search_contents(self.file_lines, "Reference Line Name", expect_one=False, require_one=False)
        return [ref_line.strip() for ref_line in ref_lines]

    @cached_property
    def ref_point_names(self) -> list[str]:
        """A list of reference point names contained in the HEC-RAS geometry file."""
        ref_points = search_contents(self.file_lines, "Reference Point Name", expect_one=False, require_one=False)
        return [ref_point.strip() for ref_point in ref_points]

    @cached_property
    def connections(self) -> dict[str, Connection]:
        """A dictionary of the SA/2D connections contained in the HEC-RAS geometry file."""
        matches = search_contents(self.file_lines, "Connection", expect_one=False, require_one=False)
        connections = []
        for line in matches:
            if "," in line:
                parts = line.split(",")
                connections.append(parts[0].strip())
            else:
                connections.append(line)
        return {c: Connection(c) for c in connections}

    @cached_property
    def reach_gdf(self):
        """A GeodataFrame of the reaches contained in the HEC-RAS geometry file."""
        if self.reaches.values():
            return gpd.GeoDataFrame(pd.concat([reach.gdf for reach in self.reaches.values()], ignore_index=True))
        else:
            return None

    @cached_property
    def junction_gdf(self):
        """A GeodataFrame of the junctions contained in the HEC-RAS geometry file."""
        if self.junctions:
            return gpd.GeoDataFrame(
                pd.concat(
                    [junction.gdf for junction in self.junctions.values()],
                    ignore_index=True,
                )
            )

    @cached_property
    def xs_gdf(self) -> gpd.GeoDataFrame:
        """Geodataframe of all cross sections in the geometry text file."""
        xs_gdf = pd.DataFrame([xs.gdf_data_dict for xs in self.cross_sections.values()])
        if len(xs_gdf) <= 0:
            return xs_gdf
        subsets = []
        for _, reach in self.reach_gdf.iterrows():
            subset_xs = xs_gdf.loc[xs_gdf["river_reach"] == reach["river_reach"]].copy()
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
        gdf = gpd.GeoDataFrame(pd.concat(subsets))
        return self.determine_lateral_structure_xs(gdf)

    @cached_property
    def structures_gdf(self) -> gpd.GeoDataFrame:
        """Geodataframe of all structures in the geometry text file."""
        if len(self.structures) > 0:
            return gpd.GeoDataFrame(
                pd.concat([structure.gdf for structure in self.structures.values()], ignore_index=True)
            )
        else:
            return None

    @cached_property
    def concave_hull_gdf(self) -> gpd.GeoDataFrame:
        """Convert shapely convave hull to geopandas."""
        return gpd.GeoDataFrame({"geometry": [self.concave_hull]}, geometry="geometry")

    @cached_property
    def concave_hull(self):
        """Compute and return the concave hull (polygon) for cross sections."""
        polygons = []
        xs_df = self.xs_gdf  # shorthand
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

        if self.junction_gdf is not None:
            for _, j in self.junction_gdf.iterrows():
                polygons.append(self.junction_hull(xs_df, j))
        out_hull = self.clean_polygons(polygons)
        return out_hull

    def clean_polygons(self, polygons: list) -> list:
        """Unpack geometry collections, make polygons valid, and remove internal rings."""
        polys = self.unpack_geoms(polygons)
        polys = [make_valid(i) for i in polys]
        unioned = union_all(polys)
        unioned = self.remove_holes(unioned)
        return unioned

    def unpack_geoms(self, geoms: list):
        """Unpack multipolygons and geometry collections to polygon list."""
        out = []
        for i in geoms:
            if isinstance(i, GeometryCollection):
                for j in i.geoms:
                    out.extend(self.unpack_geoms([j]))
            elif isinstance(i, MultiPolygon):
                out.extend([j for j in i.geoms])
            else:
                out.append(i)
        return out

    def remove_holes(self, geom):
        """Remove internal holes in polygons."""
        if isinstance(geom, Polygon):
            return Polygon(geom.exterior)
        elif isinstance(geom, MultiPolygon):
            return MultiPolygon([Polygon(p.exterior) for p in geom.geoms])
        return geom

    def junction_hull(self, xs_gdf: gpd.GeoDataFrame, junction: gpd.GeoSeries) -> Polygon:
        """Compute and return the concave hull (polygon) for a juction."""
        junction_xs = self.determine_junction_xs(xs_gdf, junction)

        junction_xs["start"] = junction_xs.apply(lambda row: row.geometry.boundary.geoms[0], axis=1)
        junction_xs["end"] = junction_xs.apply(lambda row: row.geometry.boundary.geoms[1], axis=1)
        junction_xs["to_line"] = junction_xs.apply(lambda row: self.determine_xs_order(row, junction_xs), axis=1)

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

    def determine_junction_xs(self, xs_gdf: gpd.GeoDataFrame, junction: gpd.GeoSeries) -> gpd.GeoDataFrame:
        """Determine the cross sections that bound a junction."""
        junction_xs = []
        for us_river, us_reach in zip(junction.us_rivers.split(","), junction.us_reaches.split(",")):
            xs_us_river_reach = xs_gdf[(xs_gdf["river"] == us_river) & (xs_gdf["reach"] == us_reach)]
            junction_xs.append(
                xs_us_river_reach[xs_us_river_reach["river_station"] == xs_us_river_reach["river_station"].min()]
            )
        for ds_river, ds_reach in zip(junction.ds_rivers.split(","), junction.ds_reaches.split(",")):
            xs_ds_river_reach = xs_gdf[(xs_gdf["river"] == ds_river) & (xs_gdf["reach"] == ds_reach)].copy()
            xs_ds_river_reach["geometry"] = xs_ds_river_reach.reverse()
            junction_xs.append(
                xs_ds_river_reach[xs_ds_river_reach["river_station"] == xs_ds_river_reach["river_station"].max()]
            )
        return pd.concat(junction_xs).copy()

    def determine_xs_order(self, row: gpd.GeoSeries, junction_xs: gpd.gpd.GeoDataFrame):
        """Detemine what order cross sections bounding a junction should be in to produce a valid polygon."""
        candidate_lines = junction_xs[junction_xs["river_reach_rs"] != row["river_reach_rs"]]
        candidate_lines["distance"] = candidate_lines["start"].distance(row.end)
        return candidate_lines.loc[
            candidate_lines["distance"] == candidate_lines["distance"].min(),
            "river_reach_rs",
        ].iloc[0]

    def determine_lateral_structure_xs(self, xs_gdf):
        """Determine if the cross sections are connected to lateral structure.

        Determine if the cross sections are connected to lateral structures,
        if they are update 'has_lateral_structures' to True.
        """
        for structure in self.structures.values():
            if structure.type == StructureType.LATERAL_STRUCTURE:
                try:
                    us_rs = xs_gdf.loc[
                        (xs_gdf["river"] == structure.river)
                        & (xs_gdf["reach"] == structure.reach)
                        & (xs_gdf["river_station"] > structure.river_station),
                        "river_station",
                    ].min()

                    ds_xs = xs_gdf.loc[
                        (xs_gdf["river"] == structure.river)
                        & (xs_gdf["reach"] == structure.reach)
                        & (xs_gdf["river_station"] <= us_rs)
                    ]

                    reach_len = 0
                    river_stations = []
                    for _, row in ds_xs.iterrows():
                        reach_len += row["channel_reach_length"]
                        river_stations.append(row.river_station)
                        if reach_len > structure.distance_to_us_xs + structure.weir_length:
                            break

                    xs_gdf.loc[
                        (xs_gdf["river"] == structure.river)
                        & (xs_gdf["reach"] == structure.reach)
                        & (xs_gdf["river_station"] <= max(river_stations))
                        & (xs_gdf["river_station"] >= min(river_stations)),
                        "has_lateral_structure",
                    ] = True

                    if structure.tail_water_river in xs_gdf.river:
                        if structure.multiple_xs:
                            ds_xs = xs_gdf.loc[
                                (xs_gdf["river"] == structure.tail_water_river)
                                & (xs_gdf["reach"] == structure.tail_water_reach)
                                & (xs_gdf["river_station"] <= structure.tail_water_river_station)
                            ]

                            reach_len = 0
                            river_stations = []
                            for _, row in ds_xs.iterrows():
                                reach_len += row["channel_reach_length"]
                                river_stations.append(row.river_station)
                                if reach_len > structure.tw_distance + structure.weir_length:
                                    break

                            xs_gdf.loc[
                                (xs_gdf["river"] == structure.tail_water_river)
                                & (xs_gdf["reach"] == structure.tail_water_reach)
                                & (xs_gdf["river_station"] <= max(river_stations))
                                & (xs_gdf["river_station"] >= min(river_stations)),
                                "has_lateral_structure",
                            ] = True
                        else:
                            ds_xs = xs_gdf.loc[
                                (xs_gdf["river"] == structure.tail_water_river)
                                & (xs_gdf["reach"] == structure.tail_water_reach)
                                & (xs_gdf["river_station"] <= structure.tail_water_river_station)
                            ]

                            reach_len = 0
                            river_stations = []
                            for _, row in ds_xs.iterrows():
                                reach_len += row["channel_reach_length"]
                                river_stations.append(row.river_station)
                                if len(river_stations) > 1:
                                    break

                            xs_gdf.loc[
                                (xs_gdf["river"] == structure.tail_water_river)
                                & (xs_gdf["reach"] == structure.tail_water_reach)
                                & (xs_gdf["river_station"] <= max(river_stations))
                                & (xs_gdf["river_station"] >= min(river_stations)),
                                "has_lateral_structure",
                            ] = True

                except InvalidStructureDataError:
                    pass

        return xs_gdf

    def get_subtype_gdf(self, subtype: str) -> gpd.GeoDataFrame:
        """Get a geodataframe of a specific subtype of geometry asset."""
        tmp_objs: dict[str] = getattr(self, subtype)
        return gpd.GeoDataFrame(
            pd.concat([obj.gdf for obj in tmp_objs.values()], ignore_index=True)
        )  # TODO: may need to add some logic here for empty dicts

    def iter_labeled_gdfs(self) -> Iterator[tuple[str, gpd.GeoDataFrame]]:
        """Return gdf and associated property."""
        for property in self.PROPERTIES_WITH_GDF:
            gdf = self.get_subtype_gdf(property)
            yield property, gdf

    def to_gpkg(self, gpkg_path: str) -> None:
        """Write the HEC-RAS Geometry file to geopackage."""
        for subtype, gdf in self.iter_labeled_gdfs():
            gdf.to_file(gpkg_path, driver="GPKG", layer=subtype, ignore_index=True)


class SteadyFlowFile(CachedFile):
    """HEC-RAS Steady Flow file data."""

    @cached_property
    def flow_title(self) -> str:
        """Return flow title."""
        return search_contents(self.file_lines, "Flow Title", expect_one=True, require_one=False)

    @cached_property
    def n_profiles(self) -> int:
        """Return number of profiles."""
        return int(search_contents(self.file_lines, "Number of Profiles"))

    @cached_property
    def n_flow_change_locations(self):
        """Number of flow change locations."""
        return len(search_contents(self.file_lines, "River Rch & RM", expect_one=False))

    @cached_property
    def profile_names(self):
        """Profile names."""
        return search_contents(self.file_lines, "Profile Names").split(",")

    @cached_property
    def flow_change_locations(self):
        """Retrieve flow change locations."""
        flow_change_locations = []
        tmp_n_flow_change_locations = self.n_flow_change_locations
        for ind, location in enumerate(search_contents(self.file_lines, "River Rch & RM", expect_one=False)):
            # parse river, reach, and river station for the flow change location
            river, reach, rs = location.split(",")
            lines = text_block_from_start_end_str(
                f"River Rch & RM={location}",
                ["River Rch & RM", "Boundary for River Rch & Prof#"],
                self.file_lines,
            )
            flows = []

            for line in lines[1:]:
                if "River Rch & RM" in line:
                    break

                for i in range(0, len(line), 8):
                    tmp_str = line[i : i + 8].lstrip(" ")
                    if len(tmp_str) == 0:
                        tmp_n_flow_change_locations -= 1  # invalid entry
                        if len(flow_change_locations) == tmp_n_flow_change_locations:
                            return flow_change_locations
                        else:
                            break
                    flows.append(float(tmp_str))
                    if len(flows) == self.n_profiles:
                        flow_change_locations.append(
                            {
                                "river": river.strip(" "),
                                "reach": reach.strip(" "),
                                "rs": float(rs),
                                "flows": flows,
                                "profile_names": self.profile_names,
                            }
                        )
                    if len(flow_change_locations) == tmp_n_flow_change_locations:
                        return flow_change_locations


@dataclass
class FlowChangeLocation:
    """HEC-RAS Flow Change Locations."""

    river: Optional[str] = None
    reach: Optional[str] = None
    rs: Optional[str] = None
    flows: Optional[list[float]] = None
    profile_names: Optional[list[str]] = None


class UnsteadyFlowFile(CachedFile):
    """HEC-RAS Unsteady Flow file data."""

    @cached_property
    def flow_title(self) -> str:
        """Return flow title."""
        return search_contents(self.file_lines, "Flow Title")

    @cached_property
    def boundary_locations(self) -> list:
        """Return boundary locations.

        Example file line:
        Boundary Location=                ,                ,        ,        ,                ,Perimeter 1     ,                ,PugetSound_Ocean_Boundary       ,
        """
        boundary_dict = []
        matches = search_contents(self.file_lines, "Boundary Location", expect_one=False, require_one=False)
        for line in matches:
            parts = line.split(",")
            if len(parts) >= 7:
                flow_area = parts[5].strip()
                bc_line = parts[7].strip()
                if bc_line:
                    boundary_dict.append({flow_area: bc_line})
        return boundary_dict

    @cached_property
    def reference_lines(self):
        """Return reference lines."""
        return search_contents(
            self.file_lines, "Observed Rating Curve=Name=Ref Line", token=":", expect_one=False, require_one=False
        )

    @cached_property
    def precip_bc(self):
        """Return precipitation boundary condition."""
        return search_contents(
            self.file_lines,
            "Met BC=Precipitation|",
            token="Gridded DSS Pathname=",
            expect_one=False,
            require_one=False,
        )


class QuasiUnsteadyFlowFile(CachedFile):
    """HEC-RAS Quasi-Unsteady Flow file data."""

    # TODO: implement this class
    pass


class RASHDFFile(CachedFile):
    """Base class for parsing HDF assets (Plan and Geometry HDF files)."""

    _hdf_constructor = RasHdf
    hdf_object: RasHdf

    def __init__(self, fpath):
        # Prevent reinitialization if the instance is already cached
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True
        self.fpath = fpath

        self.hdf_object = self._hdf_constructor.open_uri(
            fpath,
            fsspec_kwargs={
                "default_cache_type": "blockcache",
                "default_block_size": 10**5,
                "anon": (os.getenv("AWS_ACCESS_KEY_ID") is None and os.getenv("AWS_SECRET_ACCESS_KEY") is None),
            },
        )
        self._root_attrs: dict | None = None
        self._geom_attrs: dict | None = None
        self._structures_attrs: dict | None = None
        self._2d_flow_attrs: dict | None = None

    @cached_property
    def file_version(self) -> str | None:
        """Return File Version."""
        if self._root_attrs == None:
            self._root_attrs = self.hdf_object.get_root_attrs()
        return self._root_attrs.get("File Version")

    @cached_property
    def units_system(self) -> str | None:
        """Return Units System."""
        if self._root_attrs == None:
            self._root_attrs = self.hdf_object.get_root_attrs()
        return self._root_attrs.get("Units System")


class PlanOrGeomHDFFile(RASHDFFile):
    """Mostly geometry-accessing functions for data present in both plan and geom files."""

    @cached_property
    def geometry_time(self) -> datetime.datetime | None:
        """Return Geometry Time."""
        if self._geom_attrs == None:
            self._geom_attrs = self.hdf_object.get_geom_attrs()
        return self._geom_attrs.get("Geometry Time")

    @cached_property
    def landcover_date_last_modified(self) -> datetime.datetime | None:
        """Return Land Cover Date Last Modified."""
        if self._geom_attrs == None:
            self._geom_attrs = self.hdf_object.get_geom_attrs()
        return self._geom_attrs.get("Land Cover Date Last Modified")

    @cached_property
    def landcover_filename(self) -> str | None:
        """Return Land Cover Filename."""
        if self._geom_attrs == None:
            self._geom_attrs = self.hdf_object.get_geom_attrs()
        return self._geom_attrs.get("Land Cover Filename")

    @cached_property
    def landcover_layername(self) -> str | None:
        """Return Land Cover Layername."""
        if self._geom_attrs == None:
            self._geom_attrs = self.hdf_object.get_geom_attrs()
        return self._geom_attrs.get("Land Cover Layername")

    @cached_property
    def rasmapperlibdll_date(self) -> datetime.datetime | None:
        """Return RasMapperLib.dll Date."""
        if self._geom_attrs == None:
            self._geom_attrs = self.hdf_object.get_geom_attrs()
        return self._geom_attrs.get("RasMapperLib.dll Date").isoformat()

    @cached_property
    def si_units(self) -> bool | None:
        """Return SI Units."""
        if self._geom_attrs == None:
            self._geom_attrs = self.hdf_object.get_geom_attrs()
        return self._geom_attrs.get("SI Units")

    @cached_property
    def terrain_file_date(self) -> datetime.datetime | None:
        """Return Terrain File Date."""
        if self._geom_attrs == None:
            self._geom_attrs = self.hdf_object.get_geom_attrs()
        return self._geom_attrs.get("Terrain File Date").isoformat()

    @cached_property
    def terrain_filename(self) -> str | None:
        """Return Terrain Filename."""
        if self._geom_attrs == None:
            self._geom_attrs = self.hdf_object.get_geom_attrs()
        return self._geom_attrs.get("Terrain Filename")

    @cached_property
    def terrain_layername(self) -> str | None:
        """Return Terrain Layername."""
        if self._geom_attrs == None:
            self._geom_attrs = self.hdf_object.get_geom_attrs()
        return self._geom_attrs.get("Terrain Layername")

    @cached_property
    def geometry_version(self) -> str | None:
        """Return Version."""
        if self._geom_attrs == None:
            self._geom_attrs = self.hdf_object.get_geom_attrs()
        return self._geom_attrs.get("Version")

    @cached_property
    def bridges_culverts(self) -> int | None:
        """Return Bridge/Culvert Count."""
        if self._structures_attrs == None:
            self._structures_attrs = self.hdf_object.get_geom_structures_attrs()
        return self._structures_attrs.get("Bridge/Culvert Count")

    @cached_property
    def connections(self) -> int | None:
        """Return Connection Count."""
        if self._structures_attrs == None:
            self._structures_attrs = self.hdf_object.get_geom_structures_attrs()
        return self._structures_attrs.get("Connection Count")

    @cached_property
    def inline_structures(self) -> int | None:
        """Return Inline Structure Count."""
        if self._structures_attrs == None:
            self._structures_attrs = self.hdf_object.get_geom_structures_attrs()
        return self._structures_attrs.get("Inline Structure Count")

    @cached_property
    def lateral_structures(self) -> int | None:
        """Return Lateral Structure Count."""
        if self._structures_attrs == None:
            self._structures_attrs = self.hdf_object.get_geom_structures_attrs()
        return self._structures_attrs.get("Lateral Structure Count")

    @cached_property
    def two_d_flow_cell_average_size(self) -> float | None:
        """Return Cell Average Size."""
        if self._2d_flow_attrs == None:
            self._2d_flow_attrs = self.hdf_object.get_geom_2d_flow_area_attrs()
        return int(np.sqrt(self._2d_flow_attrs.get("Cell Average Size")))

    @cached_property
    def two_d_flow_cell_maximum_index(self) -> int | None:
        """Return Cell Maximum Index."""
        if self._2d_flow_attrs == None:
            self._2d_flow_attrs = self.hdf_object.get_geom_2d_flow_area_attrs()
        return self._2d_flow_attrs.get("Cell Maximum Index")

    @cached_property
    def two_d_flow_cell_maximum_size(self) -> int | None:
        """Return Cell Maximum Size."""
        if self._2d_flow_attrs == None:
            self._2d_flow_attrs = self.hdf_object.get_geom_2d_flow_area_attrs()
        return int(np.sqrt(self._2d_flow_attrs.get("Cell Maximum Size")))

    @cached_property
    def two_d_flow_cell_minimum_size(self) -> int | None:
        """Return Cell Minimum Size."""
        if self._2d_flow_attrs == None:
            self._2d_flow_attrs = self.hdf_object.get_geom_2d_flow_area_attrs()
        return int(np.sqrt(self._2d_flow_attrs.get("Cell Minimum Size")))

    def mesh_areas(self, crs: str = None, return_gdf: bool = False) -> gpd.GeoDataFrame | Polygon | MultiPolygon:
        """Retrieve and process mesh area geometries.

        Parameters
        ----------
        crs : str, optional
            The coordinate reference system (CRS) to set if the mesh areas do not have one. Defaults to None
        return_gdf : bool, optional
            If True, returns a GeoDataFrame of the mesh areas. If False, returns a unified Polygon or Multipolygon geometry. Defaults to False.

        """
        mesh_areas = self.hdf_object.mesh_areas()
        if mesh_areas is None or mesh_areas.empty:
            return Polygon()

        if mesh_areas.crs is None and crs is not None:
            mesh_areas = mesh_areas.set_crs(crs)

        if return_gdf:
            return mesh_areas
        else:
            geometries = mesh_areas["geometry"]
            return unary_union(geometries)

    @cached_property
    def breaklines(self) -> gpd.GeoDataFrame | None:
        """Return breaklines."""
        breaklines = self.hdf_object.breaklines()

        if breaklines is None or breaklines.empty:
            raise ValueError("No breaklines found.")

        return breaklines

    @cached_property
    def mesh_cells(self) -> gpd.GeoDataFrame | None:
        """Return mesh cell polygons."""
        mesh_cells = self.hdf_object.mesh_cell_polygons()

        if mesh_cells is None or mesh_cells.empty:
            raise ValueError("No mesh cells found.")

        return mesh_cells

    @cached_property
    def bc_lines(self) -> gpd.GeoDataFrame | None:
        """Return boundary condition lines."""
        bc_lines = self.hdf_object.bc_lines()

        if bc_lines is None or bc_lines.empty:
            raise ValueError("No boundary condition lines found.")

        return bc_lines


class PlanHDFFile(PlanOrGeomHDFFile):
    """Class to parse data from Plan HDF files."""

    _hdf_constructor = RasPlanHdf
    hdf_object: RasPlanHdf

    def __init__(self, fpath: str, **kwargs):
        super().__init__(fpath, **kwargs)

        self._plan_info_attrs = None
        self._plan_parameters_attrs = None
        self._meteorology_attrs = None

    @cached_property
    def plan_information_base_output_interval(self) -> str | None:
        """Return Base Output Interval."""
        if self._plan_info_attrs == None:
            self._plan_info_attrs = self.hdf_object.get_plan_info_attrs()
        return self._plan_info_attrs.get("Base Output Interval")

    @cached_property
    def plan_information_computation_time_step_base(self):
        """Return Computation Time Step Base."""
        if self._plan_info_attrs == None:
            self._plan_info_attrs = self.hdf_object.get_plan_info_attrs()
        return self._plan_info_attrs.get("Computation Time Step Base")

    @cached_property
    def plan_information_flow_filename(self):
        """Return Flow Filename."""
        if self._plan_info_attrs == None:
            self._plan_info_attrs = self.hdf_object.get_plan_info_attrs()
        return self._plan_info_attrs.get("Flow Filename")

    @cached_property
    def plan_information_geometry_filename(self):
        """Return Geometry Filename."""
        if self._plan_info_attrs == None:
            self._plan_info_attrs = self.hdf_object.get_plan_info_attrs()
        return self._plan_info_attrs.get("Geometry Filename")

    @cached_property
    def plan_information_plan_filename(self):
        """Return Plan Filename."""
        if self._plan_info_attrs == None:
            self._plan_info_attrs = self.hdf_object.get_plan_info_attrs()
        return self._plan_info_attrs.get("Plan Filename")

    @cached_property
    def plan_information_plan_name(self):
        """Return Plan Name."""
        if self._plan_info_attrs == None:
            self._plan_info_attrs = self.hdf_object.get_plan_info_attrs()
        return self._plan_info_attrs.get("Plan Name")

    @cached_property
    def plan_information_project_filename(self):
        """Return Project Filename."""
        if self._plan_info_attrs == None:
            self._plan_info_attrs = self.hdf_object.get_plan_info_attrs()
        return self._plan_info_attrs.get("Project Filename")

    @cached_property
    def plan_information_project_title(self):
        """Return Project Title."""
        if self._plan_info_attrs == None:
            self._plan_info_attrs = self.hdf_object.get_plan_info_attrs()
        return self._plan_info_attrs.get("Project Title")

    @cached_property
    def plan_information_simulation_end_time(self):
        """Return Simulation End Time."""
        if self._plan_info_attrs == None:
            self._plan_info_attrs = self.hdf_object.get_plan_info_attrs()
        t = self._plan_info_attrs.get("Simulation End Time")
        if t is None:
            return None
        else:
            return t.isoformat()

    @cached_property
    def plan_information_simulation_start_time(self):
        """Return Simulation Start Time."""
        if self._plan_info_attrs == None:
            self._plan_info_attrs = self.hdf_object.get_plan_info_attrs()
        t = self._plan_info_attrs.get("Simulation Start Time")
        if t is None:
            return None
        else:
            return t.isoformat()

    @cached_property
    def plan_parameters_1d_flow_tolerance(self):
        """Return 1D Flow Tolerance."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D Flow Tolerance")

    @cached_property
    def plan_parameters_1d_maximum_iterations(self):
        """Return 1D Maximum Iterations."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D Maximum Iterations")

    @cached_property
    def plan_parameters_1d_maximum_iterations_without_improvement(self):
        """Return 1D Maximum Iterations Without Improvement."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D Maximum Iterations Without Improvement")

    @cached_property
    def plan_parameters_1d_maximum_water_surface_error_to_abort(self):
        """Return 1D Maximum Water Surface Error To Abort."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D Maximum Water Surface Error To Abort")

    @cached_property
    def plan_parameters_1d_storage_area_elevation_tolerance(self):
        """Return 1D Storage Area Elevation Tolerance."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D Storage Area Elevation Tolerance")

    @cached_property
    def plan_parameters_1d_theta(self):
        """Return 1D Theta."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D Theta")

    @cached_property
    def plan_parameters_1d_theta_warmup(self):
        """Return 1D Theta Warmup."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D Theta Warmup")

    @cached_property
    def plan_parameters_1d_water_surface_elevation_tolerance(self):
        """Return 1D Water Surface Elevation Tolerance."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D Water Surface Elevation Tolerance")

    @cached_property
    def plan_parameters_1d2d_gate_flow_submergence_decay_exponent(self):
        """Return 1D-2D Gate Flow Submergence Decay Exponent."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D-2D Gate Flow Submergence Decay Exponent")

    @cached_property
    def plan_parameters_1d2d_is_stablity_factor(self):
        """Return 1D-2D IS Stablity Factor."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D-2D IS Stablity Factor")

    @cached_property
    def plan_parameters_1d2d_ls_stablity_factor(self):
        """Return 1D-2D LS Stablity Factor."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D-2D LS Stablity Factor")

    @cached_property
    def plan_parameters_1d2d_maximum_number_of_time_slices(self):
        """Return 1D-2D Maximum Number of Time Slices."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D-2D Maximum Number of Time Slices")

    @cached_property
    def plan_parameters_1d2d_minimum_time_step_for_slicinghours(self):
        """Return 1D-2D Minimum Time Step for Slicing(hours)."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D-2D Minimum Time Step for Slicing(hours)")

    @cached_property
    def plan_parameters_1d2d_number_of_warmup_steps(self):
        """Return 1D-2D Number of Warmup Steps."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D-2D Number of Warmup Steps")

    @cached_property
    def plan_parameters_1d2d_warmup_time_step_hours(self):
        """Return 1D-2D Warmup Time Step (hours)."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D-2D Warmup Time Step (hours)")

    @cached_property
    def plan_parameters_1d2d_weir_flow_submergence_decay_exponent(self):
        """Return 1D-2D Weir Flow Submergence Decay Exponent."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D-2D Weir Flow Submergence Decay Exponent")

    @cached_property
    def plan_parameters_1d2d_maxiter(self):
        """Return 1D2D MaxIter."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("1D2D MaxIter")

    @cached_property
    def plan_parameters_2d_equation_set(self):
        """Return 2D Equation Set."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("2D Equation Set")

    @cached_property
    def plan_parameters_2d_names(self):
        """Return 2D Names."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("2D Names")

    @cached_property
    def plan_parameters_2d_volume_tolerance(self):
        """Return 2D Volume Tolerance."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("2D Volume Tolerance")

    @cached_property
    def plan_parameters_2d_water_surface_tolerance(self):
        """Return 2D Water Surface Tolerance."""
        if self._plan_parameters_attrs == None:
            self._plan_parameters_attrs = self.hdf_object.get_plan_param_attrs()
        return self._plan_parameters_attrs.get("2D Water Surface Tolerance")

    @cached_property
    def meteorology_dss_filename(self):
        """Return meteorology precip DSS Filename."""
        if self._meteorology_attrs == None:
            self._meteorology_attrs = self.hdf_object.get_meteorology_precip_attrs()
        return self._meteorology_attrs.get("DSS Filename")

    @cached_property
    def meteorology_dss_pathname(self):
        """Return meteorology precip DSS Pathname."""
        if self._meteorology_attrs == None:
            self._meteorology_attrs = self.hdf_object.get_meteorology_precip_attrs()
        return self._meteorology_attrs.get("DSS Pathname")

    @cached_property
    def meteorology_data_type(self):
        """Return meteorology precip Data Type."""
        if self._meteorology_attrs == None:
            self._meteorology_attrs = self.hdf_object.get_meteorology_precip_attrs()
        return self._meteorology_attrs.get("Data Type")

    @cached_property
    def meteorology_mode(self):
        """Return meteorology precip Mode."""
        if self._meteorology_attrs == None:
            self._meteorology_attrs = self.hdf_object.get_meteorology_precip_attrs()
        return self._meteorology_attrs.get("Mode")

    @cached_property
    def meteorology_raster_cellsize(self):
        """Return meteorology precip Raster Cellsize."""
        if self._meteorology_attrs == None:
            self._meteorology_attrs = self.hdf_object.get_meteorology_precip_attrs()
        return self._meteorology_attrs.get("Raster Cellsize")

    @cached_property
    def meteorology_source(self):
        """Return meteorology precip Source."""
        if self._meteorology_attrs == None:
            self._meteorology_attrs = self.hdf_object.get_meteorology_precip_attrs()
        return self._meteorology_attrs.get("Source")

    @cached_property
    def meteorology_units(self):
        """Return meteorology precip units."""
        if self._meteorology_attrs == None:
            self._meteorology_attrs = self.hdf_object.get_meteorology_precip_attrs()
        return self._meteorology_attrs.get("Units")


class GeometryHDFFile(PlanOrGeomHDFFile):
    """Class to parse data from Geometry HDF files."""

    _hdf_constructor = RasGeomHdf
    hdf_object: RasGeomHdf

    def __init__(self, fpath: str, **kwargs):
        super().__init__(fpath, **kwargs)

        self._plan_info_attrs = None
        self._plan_parameters_attrs = None
        self._meteorology_attrs = None

    @cached_property
    def projection(self):
        """Return geometry projection."""
        return self.hdf_object.projection()

    @cached_property
    def cross_sections(self) -> int | None:
        """Return geometry cross sections."""
        try:
            return self.hdf_object.cross_sections()
        except KeyError:
            return gpd.GeoDataFrame()

    @cached_property
    def reference_lines(self) -> gpd.GeoDataFrame | None:
        """Return geometry reference lines."""
        ref_lines = self.hdf_object.reference_lines()

        if ref_lines is None or ref_lines.empty:
            return None
        else:
            return ref_lines

    @cached_property
    def reference_points(self) -> gpd.GeoDataFrame | None:
        """Return geometry reference points."""
        ref_points = self.hdf_object.reference_points()

        if ref_points is None or ref_points.empty:
            return None
        else:
            return ref_points
