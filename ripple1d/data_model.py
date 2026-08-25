"""Data model for shared utilities."""

import glob
import json
import math
import os
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import List

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS
from shapely import reverse
from shapely.geometry import LineString, Point

from ripple1d.errors import InvalidStructureDataError
from ripple1d.utils.ripple_utils import (
    data_pairs_from_text_block,
    data_triplets_from_text_block,
    determine_crs_units,
    fix_reversed_xs,
    search_contents,
    text_block_from_start_end_str,
    text_block_from_start_str_length,
    text_block_from_start_str_to_empty_line,
    validate_point,
    xs_concave_hull,
)
from ripple1d.utils.s3_utils import init_s3_resources, read_json_from_s3


class RasModelStructure:
    """Base Model structure for RAS models."""

    def __init__(self, model_directory: str):
        self.model_directory = model_directory
        self.model_basename = Path(model_directory).name
        self._ras_junctions = None
        self._ras_structures = None
        self._ras_xs = None
        self._ras_rivers = None
        self._xs_concave_hull = None

    @property
    def model_name(self):
        """Model name."""
        return self.model_basename.replace(".prj", "")

    def derive_path(self, extension: str):
        """Derive path."""
        return str(Path(self.model_directory) / f"{self.model_name}{extension}")

    def file_exists(self, file_path: str) -> bool:
        """Check if file exists."""
        if os.path.exists(file_path):
            return True
        return False

    @property
    def ras_project_file(self):
        """RAS Project file."""
        return self.derive_path(".prj")

    @property
    def ras_gpkg_file(self):
        """RAS GeoPackage file."""
        return self.derive_path(".gpkg")

    @property
    def ras_xs(self):
        """RAS XS Geodataframe."""
        if self._ras_xs is None:
            self._ras_xs = gpd.read_file(self.ras_gpkg_file, layer="XS")
        return self._ras_xs

    @property
    def ras_junctions(self):
        """RAS Junctions Geodataframe."""
        if "Junction" in fiona.listlayers(self.ras_gpkg_file):
            if self._ras_junctions is None:
                self._ras_junctions = gpd.read_file(self.ras_gpkg_file, layer="Junction")
            return self._ras_junctions

    @property
    def ras_structures(self):
        """RAS Structures Geodataframe."""
        if "Structure" in fiona.listlayers(self.ras_gpkg_file):
            if self._ras_structures is None:
                self._ras_structures = gpd.read_file(self.ras_gpkg_file, layer="Structure")
            return self._ras_structures

    @property
    def ras_rivers(self):
        """RAS Rivers Geodataframe."""
        if self._ras_rivers is None:
            self._ras_rivers = gpd.read_file(self.ras_gpkg_file, layer="River")
        return self._ras_rivers

    @property
    def xs_concave_hull(self):
        """XS Concave Hull."""
        if self._xs_concave_hull is None:
            self._xs_concave_hull = xs_concave_hull(fix_reversed_xs(self.ras_xs, self.ras_rivers))
        return self._xs_concave_hull

    @property
    def assets(self):
        """Model assets."""
        return glob.glob(f"{self.model_directory}/Terrain/*") + [
            f for f in glob.glob(f"{self.model_directory}/*") if not os.path.isdir(f)
        ]

    @property
    def terrain_assets(self):
        """Terrain assets."""
        return glob.glob(f"{self.model_directory}/Terrain/*")

    @property
    def thumbnail_png(self):
        """Thumbnail PNG."""
        return self.derive_path(".png")


class RippleSourceModel:
    """Source Model structure for Ripple to create NwmReachModel's."""

    def __init__(self, ras_project_file: str, crs: CRS):

        self.crs = crs
        self.ras_project_file = ras_project_file
        self.model_directory = Path(ras_project_file).parent.as_posix()
        self.model_basename = Path(ras_project_file).as_posix()

    @property
    def model_name(self):
        """Model name."""
        return self.model_basename.replace(".prj", "")

    def derive_path(self, extension: str):
        """Derive path."""
        return str(Path(self.model_directory) / f"{self.model_name}{extension}")

    def file_exists(self, file_path: str) -> bool:
        """Check if file exists."""
        if os.path.exists(file_path):
            return True
        return False

    @property
    def ras_gpkg_file(self):
        """RAS GeoPackage file."""
        return self.derive_path(".gpkg")

    @property
    def assets(self):
        """Model assets."""
        return glob.glob(f"{self.model_directory}/Terrain/*") + [
            f for f in glob.glob(f"{self.model_directory}/*") if not os.path.isdir(f)
        ]

    @property
    def terrain_assets(self):
        """Terrain assets."""
        return glob.glob(f"{self.model_directory}/Terrain/*")

    @property
    def thumbnail_png(self):
        """Thumbnail PNG."""
        return self.derive_path(".png")

    @property
    def conflation_file(self):
        """Conflation file."""
        return self.derive_path(".conflation.json")

    def nwm_conflation_parameters(self, nwm_id: str):
        """NWM Conflation parameters."""
        with open(self.conflation_file, "r") as f:
            conflation_parameters = json.loads(f.read())
        return conflation_parameters[nwm_id]


class RippleSourceDirectory:
    """Source Directory for Ripple to create NwmReachModel's. Should contain the conflation.json file and gpkg file for the source model."""

    def __init__(self, source_directory: str, model_name: str):

        self.source_directory = source_directory
        self.model_basename = os.path.basename(self.source_directory)
        self.model_name = model_name

    def derive_path(self, extension: str):
        """Derive path."""
        return str(Path(self.source_directory) / f"{self.model_name}{extension}")

    def file_exists(self, file_path: str) -> bool:
        """Check if file exists."""
        if os.path.exists(file_path):
            return True
        return False

    @property
    def ras_gpkg_file(self):
        """RAS GeoPackage file."""
        return self.derive_path(".gpkg")

    @property
    def ras_project_file(self):
        """RAS Project file."""
        return self.derive_path(".prj")

    @property
    def assets(self):
        """Model assets."""
        return glob.glob(f"{self.source_directory}/Terrain/*") + [
            f for f in glob.glob(f"{self.source_directory}/*") if not os.path.isdir(f)
        ]

    @property
    def terrain_assets(self):
        """Terrain assets."""
        return glob.glob(f"{self.source_directory}/Terrain/*")

    @property
    def thumbnail_png(self):
        """Thumbnail PNG."""
        return self.derive_path(".png")

    @property
    def conflation_file(self):
        """Conflation file."""
        return self.derive_path(".conflation.json")

    def nwm_conflation_parameters(self, nwm_id: str):
        """NWM Conflation parameters."""
        with open(self.conflation_file, "r") as f:
            conflation_parameters = json.loads(f.read())
        return conflation_parameters["reaches"][nwm_id]

    @property
    def source_model_metadata(self):
        """Metadata for the source model."""
        with open(self.conflation_file, "r") as f:
            conflation_parameters = json.loads(f.read())
        return conflation_parameters["metadata"]


class NwmReachModel(RasModelStructure):
    """National Water Model reach-based HEC-RAS Model files and directory structure."""

    def __init__(self, model_directory: str, library_directory: str = ""):
        super().__init__(model_directory)
        self.library_directory = library_directory

    @property
    def terrain_directory(self):
        """Terrain directory."""
        return str(Path(self.model_directory) / "Terrain")

    @property
    def ras_terrain_hdf(self):
        """RAS Terrain HDF file."""
        return str(Path(self.terrain_directory) / f"{self.model_name}.hdf")

    @property
    def fim_results_directory(self):
        """FIM results directory."""
        return str(Path(self.library_directory) / self.model_name)

    @property
    def fim_lib_assets(self):
        """Assets of the fim library."""
        return glob.glob(f"{self.fim_results_directory}/*/*")

    @property
    def fim_lib_stac_json_file(self):
        """FIM LIBRARY STAC JSON file."""
        return str(Path(self.fim_results_directory) / f"{self.model_name}.fim_lib.stac.json")

    @property
    def fim_results_database(self):
        """Results database."""
        return str(Path(self.library_directory) / f"{self.model_name}.db")

    @property
    def fim_scenarios(self):
        """FIM scenarios."""
        with sqlite3.connect(self.fim_results_database) as conn:
            cursor = conn.cursor()
            sql_query = f"""SELECT us_flow, us_wse, ds_wse
            FROM scenarios
            WHERE reach_id={self.model_name}"""
            cursor.execute(sql_query)

            data = cursor.fetchall()
        data_list = ["Flow | US WSE | DS WSE"]
        for row in data:
            data_list.append(" | ".join([str(r) for r in row]))

        return data_list

    @property
    def crs(self):
        """Coordinate Reference System."""
        return self.ripple1d_parameters["crs"]

    def upload_files_to_s3(self, ras_s3_prefix: str, bucket: str):
        """Upload the model to s3."""
        _, client, _ = init_s3_resources()
        for file in self.assets:
            key = str(PurePosixPath(Path(file.replace(self.model_directory, ras_s3_prefix))))
            client.upload_file(
                Bucket=bucket,
                Key=key,
                Filename=file,
            )

    def upload_fim_lib_assets(self, s3_prefix: str, bucket: str):
        """Upload the fim lib to s3."""
        _, client, _ = init_s3_resources()
        for file in self.fim_lib_assets:
            parts = list(Path(file).parts[-2:])
            file_name = "-".join(parts)
            key = f"{s3_prefix}/{file_name}"

            client.upload_file(
                Bucket=bucket,
                Key=key,
                Filename=file,
            )
        file_name = os.path.basename(self.fim_lib_stac_json_file)
        client.upload_file(Bucket=bucket, Key=f"{s3_prefix}/{file_name}", Filename=self.fim_lib_stac_json_file)

    @property
    def ripple1d_parameters(self):
        """Ripple parameters."""
        with open(self.conflation_file, "r") as f:
            ripple1d_parameters = json.loads(f.read())
        return ripple1d_parameters

    @property
    def units(self):
        """Units specified in the metadata of the geopackage."""
        return self.ripple1d_parameters["source_model_metadata"]["source_ras_model"]["units"]

    @property
    def flow_file(self):
        """Flow file of the source model."""
        return self.ripple1d_parameters["source_model_metadata"]["source_ras_model"]["source_ras_files"]["forcing"]

    @property
    def flow_extension(self):
        """Extension of the source model flow file."""
        return Path(self.flow_file).suffix

    def update_write_ripple1d_parameters(self, new_parameters: dict):
        """Write Ripple parameters."""
        parameters = self.ripple1d_parameters
        parameters.update(new_parameters)
        with open(self.conflation_file, "w") as f:
            f.write(json.dumps(parameters, indent=4))

    @property
    def conflation_file(self):
        """Conflation file."""
        return self.derive_path(".ripple1d.json")

    @property
    def model_stac_json_file(self):
        """STAC JSON file."""
        return self.derive_path(".model.stac.json")

    def terrain_agreement_file(self, f: str):
        """Terrain agreement JSON file."""
        return self.derive_path(f".terrain_agreement.{f}")


@dataclass
class FlowChangeLocation:
    """HEC-RAS Flow Change Locations."""

    river: str = None
    reach: str = None
    rs: float = None
    flows: list[float] = None
    profile_names: list[str] = None


class XS:
    """HEC-RAS Cross Section."""

    def __init__(
        self,
        ras_data: list,
        river_reach: str,
        river: str,
        reach: str,
        crs: str,
        reach_geom: LineString = None,
        units: str = "English",
    ):
        self.ras_data = ras_data
        self.crs = crs
        self.river = river
        self.reach = reach
        self.river_reach = river_reach
        self.river_reach_rs = f"{river} {reach} {self.river_station}"
        self.river_reach_rs_str = f"{river} {reach} {self.river_station_str}"
        self.thalweg_drop = None
        self.reach_geom = reach_geom
        self.computed_channel_reach_length = None
        self.computed_channel_reach_length_ratio = None
        self.units = units
        self.has_lateral_structures = False

    def split_xs_header(self, position: int):
        """
        Split cross section header.

        Example: Type RM Length L Ch R = 1 ,83554.  ,237.02,192.39,113.07.
        """
        header = search_contents(self.ras_data, "Type RM Length L Ch R ", expect_one=True)

        return header.split(",")[position]

    @property
    def river_station(self):
        """Cross section river station."""
        return float(self.river_station_str.replace("*", ""))

    @property
    def river_station_str(self) -> str:
        """Return the river station with * for interpolated sections."""
        return self.split_xs_header(1).rstrip()

    @property
    def left_reach_length(self):
        """Cross section left reach length."""
        dist = self.split_xs_header(2)
        if not dist:
            return 0.0
        else:
            return float(dist)

    @property
    def channel_reach_length(self):
        """Cross section channel reach length."""
        dist = self.split_xs_header(3)
        if not dist:
            return 0.0
        else:
            return float(dist)

    @property
    def right_reach_length(self):
        """Cross section right reach length."""
        dist = self.split_xs_header(4)
        if not dist:
            return 0.0
        else:
            return float(dist)

    @property
    def number_of_coords(self):
        """Number of coordinates in cross section."""
        try:
            return int(search_contents(self.ras_data, "XS GIS Cut Line", expect_one=True))
        except ValueError as e:
            return 0
            # raise NotGeoreferencedError(f"No coordinates found for cross section: {self.river_reach_rs} ")

    @property
    def min_elevation(self):
        """The min elevaiton in the cross section."""
        if self.station_elevation_points:
            _, y = list(zip(*self.station_elevation_points))
            return min(y)

    @property
    def min_elevation_in_channel(self):
        """A boolean indicating if the minimum elevation is in the channel."""
        if self.min_elevation == self.thalweg:
            return True
        else:
            return False

    @property
    def thalweg(self):
        """The min elevation of the channel (between bank points)."""
        return self.station_elevation_df.loc[
            (self.station_elevation_df["Station"] <= self.right_bank_station)
            & (self.station_elevation_df["Station"] >= self.left_bank_station),
            "Elevation",
        ].min()

    @property
    def has_htab_error(self):
        """Check if min htab value is less than section invert."""
        if self.htab_string is None:
            return False
        else:
            return self.htab_starting_el < self.thalweg

    @property
    def htab_string(self):
        """Cross section htab string."""
        try:
            htabstr = search_contents(self.ras_data, "XS HTab Starting El and Incr", expect_one=True)
        except:
            htabstr = None
        return htabstr

    @property
    def htab_starting_el(self):
        """Cross section minimum htab."""
        return float(self.htab_string.split(",")[0])

    @property
    def htab_increment(self):
        """Cross section minimum htab."""
        return float(self.htab_string.split(",")[1])

    @property
    def htab_points(self):
        """Cross section minimum htab."""
        return float(self.htab_string.split(",")[2])

    @property
    def xs_max_elevation(self):
        """Cross section maximum elevation."""
        if self.station_elevation_points:
            _, y = list(zip(*self.station_elevation_points))
            return max(y)

    @property
    def coords(self):
        """Cross section coordinates."""
        lines = text_block_from_start_str_length(
            f"XS GIS Cut Line={self.number_of_coords}",
            math.ceil(self.number_of_coords / 2),
            self.ras_data,
        )
        if lines:
            return data_pairs_from_text_block(lines, 32)

    @property
    def number_of_station_elevation_points(self):
        """Number of station elevation points."""
        return int(search_contents(self.ras_data, "#Sta/Elev", expect_one=True))

    @property
    def station_elevation_points(self):
        """Station elevation points."""
        try:
            lines = text_block_from_start_str_length(
                f"#Sta/Elev= {self.number_of_station_elevation_points} ",
                math.ceil(self.number_of_station_elevation_points / 5),
                self.ras_data,
            )
            return data_pairs_from_text_block(lines, 16)
        except ValueError as e:
            return None

    @property
    def bank_stations(self):
        """Bank stations."""
        return search_contents(self.ras_data, "Bank Sta", expect_one=True).split(",")

    @property
    def left_bank_station(self):
        """The cross sections left bank station."""
        return float(self.bank_stations[0])

    @property
    def right_bank_station(self):
        """The cross sections right bank station."""
        return float(self.bank_stations[1])

    @property
    def station_length(self):
        """Length of cross section based on station-elevation data."""
        return self.last_station - self.first_station

    @property
    def first_station(self):
        """First station of the cross section."""
        return float(self.station_elevation_points[0][0])

    @property
    def last_station(self):
        """Last station of the cross section."""
        return float(self.station_elevation_points[-1][0])

    @property
    def cutline_length(self):
        """Length of the cross section bassed on the geometry (x-y coordinates)."""
        return self.geom.length * self.unit_conversion

    @property
    def xs_length_ratio(self):
        """Ratio of the cutline length to the station length."""
        if self.skew:
            return self.cutline_length / (self.station_length / math.cos(math.radians(self.skew)))
        else:
            return self.cutline_length / self.station_length

    @property
    @lru_cache
    def geom(self):
        """Geometry of the cross section according to its coords."""
        return LineString(self.coords)

    @property
    def banks_encompass_channel(self):
        """A boolean; True if the channel centerlien intersects the cross section between the bank stations."""
        if self.cross_section_intersects_reach:
            if (self.centerline_intersection_station + self.first_station) < self.right_bank_station and (
                self.centerline_intersection_station + self.first_station
            ) > self.left_bank_station:
                return True
            else:
                return False

    @property
    def centerline_intersection_station(self):
        """Station along the cross section where the centerline intersects it."""
        if self.cross_section_intersects_reach:
            return self.geom.project(self.centerline_intersection_point) * self.unit_conversion

    @property
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

    @property
    def cross_section_intersects_reach(self):
        """Detemine if the cross section intersects the reach, if not return False, otherwise return True."""
        return self.reach_geom.intersects(self.geom)

    @property
    def centerline_intersection_point(self):
        """A point located where the cross section and reach centerline intersect."""
        if self.cross_section_intersects_reach:
            intersection = self.reach_geom.intersection(self.geom)
            if intersection.geom_type == "Point":
                return intersection
            if intersection.geom_type == "MultiPoint":
                return intersection.geoms[0]

    @property
    def left_reach_length_ratio(self):
        """The ratio of the left reach length to the channel reach length."""
        if self.reach_lengths_populated:
            return self.left_reach_length / self.channel_reach_length

    @property
    def right_reach_length_ratio(self):
        """The ratio of the right reach length to the channel reach length."""
        if self.reach_lengths_populated:
            return self.right_reach_length / self.channel_reach_length

    @property
    def reach_lengths(self):
        """The reach lengths of the cross section."""
        return [self.right_reach_length, self.left_reach_length, self.channel_reach_length]

    @property
    def reach_lengths_populated(self):
        """A boolean indicating if all the reach lengths are poputed."""
        if np.isnan(self.reach_lengths).any():
            return False
        elif len([i for i in self.reach_lengths if i == 0]) > 0:
            return False
        else:
            return True

    @property
    def skew(self):
        """The skew applied to the cross section."""
        skew = search_contents(self.ras_data, "Skew Angle", expect_one=False)
        if len(skew) == 1:
            return float(skew[0])
        elif len(skew) > 1:
            raise ValueError(
                f"Expected only one skew value for the cross section recieved: {len(skew)}. XS: {self.river_reach_rs}"
            )

    @property
    def number_of_mannings_points(self):
        """The number of mannings points in the cross section."""
        return int(search_contents(self.ras_data, "#Mann", expect_one=True).split(",")[0])

    @property
    def mannings_code(self):
        """
        A code indicating what type of manning's values are used.

        0, -1 correspond to 3 value manning's; horizontally varying manning's values, respectively.
        """
        return int(search_contents(self.ras_data, "#Mann", expect_one=True).split(",")[1])

    @property
    def horizontal_varying_mannings(self):
        """A boolean indicating if horizontally varied mannings values are applied."""
        if self.mannings_code == -1:
            return True
        elif self.mannings_code == 0:
            return False
        else:
            return False

    @property
    def expansion_coefficient(self):
        """The expansion coefficient for the cross section."""
        return search_contents(self.ras_data, "Exp/Cntr", expect_one=True).split(",")[0]

    @property
    def contraction_coefficient(self):
        """The expansion coefficient for the cross section."""
        return search_contents(self.ras_data, "Exp/Cntr", expect_one=True).split(",")[1]

    @property
    @lru_cache
    def mannings(self):
        """The manning's values of the cross section."""
        try:
            lines = text_block_from_start_str_length(
                "#Mann=" + search_contents(self.ras_data, "#Mann", expect_one=True),
                math.ceil(self.number_of_mannings_points / 4),
                self.ras_data,
            )
            return data_triplets_from_text_block(lines, 24)
        except ValueError as e:
            print(e)
            return None

    @property
    @lru_cache
    def max_n(self):
        """The highest manning's n value used in the cross section."""
        return max(list(zip(*self.mannings))[1])

    @property
    @lru_cache
    def min_n(self):
        """The lowest manning's n value used in the cross section."""
        return min(list(zip(*self.mannings))[1])

    @property
    def has_levees(self):
        """A boolean indicating if the cross section contains levees."""
        levees = search_contents(self.ras_data, "Levee", expect_one=False)
        if len(levees) > 0:
            return True
        else:
            return False

    @property
    def has_ineffectives(self):
        """A boolean indicating if the cross section contains ineffective flow areas."""
        ineff = search_contents(self.ras_data, "#XS Ineff", expect_one=False)
        if len(ineff) > 0:
            return True
        else:
            return False

    @property
    def has_blocks(self):
        """A boolean indicating if the cross section contains blocked obstructions."""
        blocks = search_contents(self.ras_data, "#Block Obstruct", expect_one=False)
        if len(blocks) > 0:
            return True
        else:
            return False

    @property
    def channel_obstruction(self):
        """
        A boolean indicating if the channel is being blocked.

        A boolean indicating if ineffective flow area, blocked obstructions, or levees are contained
        in the channel (between bank stations).
        """

    @property
    @lru_cache
    def station_elevation_df(self):
        """A pandas DataFrame containing the station-elevation data of the cross section."""
        return pd.DataFrame(self.station_elevation_points, columns=["Station", "Elevation"])

    @property
    def left_max_elevation(self):
        """Max Elevation on the left side of the channel."""
        return self.station_elevation_df.loc[
            self.station_elevation_df["Station"] <= self.left_bank_station, "Elevation"
        ].max()

    @property
    def right_max_elevation(self):
        """Max Elevation on the right side of the channel."""
        df = pd.DataFrame(self.station_elevation_points, columns=["Station", "Elevation"])
        return df.loc[df["Station"] >= self.right_bank_station, "Elevation"].max()

    @property
    def overtop_elevation(self):
        """The elevation to at which the cross secition will be overtopped."""
        return min(self.right_max_elevation, self.left_max_elevation)

    @property
    def station_elevation_point_density(self):
        """The average spacing of the station-elevation points."""
        return self.cutline_length / self.number_of_station_elevation_points

    @property
    def channel_width(self):
        """The width of the cross section between bank points."""
        return self.right_bank_station - self.left_bank_station

    @property
    def left_bank_elevation(self):
        """Elevation of the left bank station."""
        return self.station_elevation_df.loc[
            self.station_elevation_df["Station"] == self.left_bank_station, "Elevation"
        ].iloc[0]

    @property
    def right_bank_elevation(self):
        """Elevation of the right bank station."""
        return self.station_elevation_df.loc[
            self.station_elevation_df["Station"] == self.right_bank_station, "Elevation"
        ].iloc[0]

    @property
    def channel_depth(self):
        """The depth of the channel; i.e., the depth at which the first bank station is overtoppped."""
        return min([self.left_bank_elevation, self.right_bank_elevation]) - self.thalweg

    @property
    def htab_min_elevation(self):
        """The starting elevation for the cross section's htab."""
        result = search_contents(self.ras_data, "XS HTab Starting El and Incr", expect_one=False)
        if len(result) == 1:
            return result[0].split(",")[0]

    @property
    def htab_min_increment(self):
        """The increment for the cross section's htab."""
        result = search_contents(self.ras_data, "XS HTab Starting El and Incr", expect_one=False)
        if len(result) == 1:
            return result[0].split(",")[1]

    @property
    def htab_points(self):
        """The number of points on the cross section's htab."""
        result = search_contents(self.ras_data, "XS HTab Starting El and Incr", expect_one=False)
        if len(result) == 1:
            return result[0].split(",")[2]

    def set_thalweg_drop(self, ds_thalweg):
        """Set the drop in thalweg elevation between this cross section and the downstream cross section."""
        self.thalweg_drop = self.thalweg - ds_thalweg

    def set_computed_reach_length(self, computed_river_station: float):
        """Set the channel reach length computed from the reach/xs/ds_xs geometry."""
        # if self.reach_lengths_populated and computed_river_station
        self.computed_channel_reach_length = self.computed_river_station - computed_river_station

    def set_computed_reach_length_ratio(self):
        """Set the ratio of the computed channel reach length to the model channel reach length."""
        self.computed_channel_reach_length_ratio = self.computed_channel_reach_length / self.channel_reach_length

    @property
    @lru_cache
    def computed_river_station(self):
        """The computed river stationing according to the reach geometry."""
        return reverse(self.reach_geom).project(self.centerline_intersection_point) * self.unit_conversion

    @property
    @lru_cache
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

    @property
    @lru_cache
    def unit_conversion(self):
        """Conversion factor for units based on the model units and crs units."""
        if self.crs_units in ["US survey foot", "foot"] and self.units == "English":
            return 1
        elif self.crs_units == "metre" and self.units == "Metric":
            return 1
        elif self.crs_units == "metre" and self.units == "English":
            return 1 / 3.281
        elif self.crs_units in ["US survey foot", "foot"] and self.units == "Metric":
            return 3.281

    @property
    @lru_cache
    def crs_units(self):
        """The units of the crs."""
        return determine_crs_units(self.crs)

    def set_bridge_xs(self, br: int):
        """
        Set the bridge cross section attribute.

        A value of 0 is added for non-bridge cross sections and 4, 3, 2, 1 are
        set for each of the bridge cross sections from downstream to upstream order.
        """
        self.bridge_xs = br

    @property
    @lru_cache
    def gdf(self):
        """Cross section geodataframe."""
        return gpd.GeoDataFrame(
            {
                "geometry": [self.geom],
                "river": [self.river],
                "reach": [self.reach],
                "river_reach": [self.river_reach],
                "river_station": [self.river_station],
                "river_reach_rs": [self.river_reach_rs],
                "river_reach_rs_str": [self.river_reach_rs_str],
                "thalweg": [self.thalweg],
                "xs_max_elevation": [self.xs_max_elevation],
                "left_reach_length": [self.left_reach_length],
                "right_reach_length": [self.right_reach_length],
                "channel_reach_length": [self.channel_reach_length],
                "computed_channel_reach_length": [self.computed_channel_reach_length],
                "computed_channel_reach_length_ratio": [self.computed_channel_reach_length_ratio],
                "left_reach_length_ratio": [self.left_reach_length_ratio],
                "right_reach_length_ratio": [self.right_reach_length_ratio],
                "reach_lengths_populated": [self.reach_lengths_populated],
                "ras_data": ["\n".join(self.ras_data)],
                "station_elevation_points": [self.station_elevation_points],
                "bank_stations": [self.bank_stations],
                "left_bank_station": [self.left_bank_station],
                "right_bank_station": [self.right_bank_station],
                "left_bank_elevation": [self.left_bank_elevation],
                "right_bank_elevation": [self.right_bank_elevation],
                "number_of_station_elevation_points": [self.number_of_station_elevation_points],
                "number_of_coords": [self.number_of_coords],
                "station_length": [self.station_length],
                "cutline_length": [self.cutline_length],
                "xs_length_ratio": [self.xs_length_ratio],
                "banks_encompass_channel": [self.banks_encompass_channel],
                "skew": [self.skew],
                "max_n": [self.max_n],
                "min_n": [self.min_n],
                "has_lateral_structure": [self.has_lateral_structures],
                "has_ineffective": [self.has_ineffectives],
                "has_levees": [self.has_levees],
                "has_blocks": [self.has_blocks],
                "channel_obstruction": [self.channel_obstruction],
                "thalweg_drop": [self.thalweg_drop],
                "left_max_elevation": [self.left_max_elevation],
                "right_max_elevation": [self.right_max_elevation],
                "overtop_elevation": [self.overtop_elevation],
                "min_elevation": [self.min_elevation],
                "channel_width": [self.channel_width],
                "channel_depth": [self.channel_depth],
                "station_elevation_point_density": [self.station_elevation_point_density],
                "htab_min_elevation": [self.htab_min_elevation],
                "htab_min_increment": [self.htab_min_increment],
                "htab points": [self.htab_points],
                "correct_cross_section_direction": [self.correct_cross_section_direction],
                "horizontal_varying_mannings": [self.horizontal_varying_mannings],
                "number_of_mannings_points": [self.number_of_mannings_points],
                "expansion_coefficient": [self.expansion_coefficient],
                "contraction_coefficient": [self.contraction_coefficient],
                "centerline_intersection_station": [self.centerline_intersection_station],
                "bridge_xs": [self.bridge_xs],
                "cross_section_intersects_reach": [self.cross_section_intersects_reach],
                "intersects_reach_once": [self.intersects_reach_once],
                "min_elevation_in_channel": [self.min_elevation_in_channel],
            },
            crs=self.crs,
            geometry="geometry",
        )


class Structure:
    """Structure."""

    def __init__(self, ras_data: list, river_reach: str, river: str, reach: str, crs: str, us_xs: XS):
        self.ras_data = ras_data
        self.crs = crs
        self.river = river
        self.reach = reach
        self.river_reach = river_reach
        self.river_reach_rs = f"{river} {reach} {self.river_station}"
        self.us_xs = us_xs

    def split_structure_header(self, position: int):
        """
        Split Structure header.

        Example: Type RM Length L Ch R = 3 ,83554.  ,237.02,192.39,113.07.
        """
        header = search_contents(self.ras_data, "Type RM Length L Ch R ", expect_one=True)

        return header.split(",")[position]

    @property
    @lru_cache
    def number_of_station_elevation_points(self):
        """The number of station elevation points."""
        return int(search_contents(self.ras_data, "Lateral Weir SE", expect_one=True))

    @property
    @lru_cache
    def station_elevation_points(self):
        """Station elevation points."""
        try:
            lines = text_block_from_start_str_length(
                f"Lateral Weir SE= {self.number_of_station_elevation_points} ",
                math.ceil(self.number_of_station_elevation_points / 5),
                self.ras_data,
            )
            return data_pairs_from_text_block(lines, 16)
        except ValueError as e:
            return None

    @property
    @lru_cache
    def weir_length(self):
        """The length weir."""
        if self.type == 6:
            try:
                return float(list(zip(*self.station_elevation_points))[0][-1])
            except IndexError as e:
                raise InvalidStructureDataError(
                    f"No station elevation data for: {self.river}, {self.reach}, {self.river_station}"
                )

    @property
    @lru_cache
    def river_station(self):
        """Structure river station."""
        return float(self.split_structure_header(1))

    @property
    @lru_cache
    def downstream_river_station(self):
        """The downstream head water river station of the lateral weir."""

    @property
    @lru_cache
    def distance_to_us_xs(self):
        """The distance from the upstream cross section to the start of the lateral structure."""
        try:
            return float(search_contents(self.ras_data, "Lateral Weir Distance", expect_one=True))
        except ValueError as e:
            raise InvalidStructureDataError(
                f"The weir distance for the lateral structure is not populated for: {self.river},{self.reach},{self.river_station}"
            )

    @property
    @lru_cache
    def tail_water_river(self):
        """The tail water reache's river name."""
        return search_contents(self.ras_data, "Lateral Weir End", expect_one=True).split(",")[0].rstrip()

    @property
    @lru_cache
    def tail_water_reach(self):
        """The tail water reache's reach name."""
        return search_contents(self.ras_data, "Lateral Weir End", expect_one=True).split(",")[1].rstrip()

    @property
    @lru_cache
    def tail_water_river_station(self):
        """The tail water reache's river stationing."""
        return float(search_contents(self.ras_data, "Lateral Weir End", expect_one=True).split(",")[2])

    @property
    @lru_cache
    def multiple_xs(self):
        """A boolean indicating if the tailwater is connected to multiple cross sections."""
        if search_contents(self.ras_data, "Lateral Weir TW Multiple XS", expect_one=True) == "-1":
            print(-1)
            return True
        else:
            print(0)
            return False

    @property
    @lru_cache
    def tw_distance(self):
        """The distance between the tail water upstream cross section and the lateral weir."""
        return float(
            search_contents(self.ras_data, "Lateral Weir Connection Pos and Dist", expect_one=True).split(",")[1]
        )

    @property
    @lru_cache
    def tail_water_river_ds_station(self):
        """The tail water reache's river stationing."""
        return self.tail_water_river_us_station - self.weir_length - self.tw_distance

    @property
    def type(self):
        """Structure type."""
        return int(self.split_structure_header(0))

    def structure_data(self, position: int):
        """Structure data."""
        if self.type in [2, 3, 4]:  # culvert or bridge
            data = text_block_from_start_str_length(
                "Deck Dist Width WeirC Skew NumUp NumDn MinLoCord MaxHiCord MaxSubmerge Is_Ogee", 1, self.ras_data
            )
            return data[0].split(",")[position]
        elif self.type == 5:  # inline weir
            data = text_block_from_start_str_length(
                "IW Dist,WD,Coef,Skew,MaxSub,Min_El,Is_Ogee,SpillHt,DesHd", 1, self.ras_data
            )
            return data[0].split(",")[position]
        elif self.type == 6:  # lateral structure
            return 0

    @property
    def distance(self):
        """Distance to upstream cross section."""
        return float(self.structure_data(0))

    @property
    def width(self):
        """Structure width."""
        # TODO check units of the RAS model
        return float(self.structure_data(1))

    @property
    @lru_cache
    def gdf(self):
        """Structure geodataframe."""
        return gpd.GeoDataFrame(
            {
                "geometry": [LineString(self.us_xs.coords).offset_curve(self.distance)],
                "river": [self.river],
                "reach": [self.reach],
                "river_reach": [self.river_reach],
                "river_station": [self.river_station],
                "river_reach_rs": [self.river_reach_rs],
                "type": [self.type],
                "distance": [self.distance],
                "width": [self.width],
                "ras_data": ["\n".join(self.ras_data)],
            },
            crs=self.crs,
            geometry="geometry",
        )


class Reach:
    """HEC-RAS River Reach."""

    def __init__(self, ras_data: list, river_reach: str, crs: str, units: str):
        reach_lines = text_block_from_start_end_str(f"River Reach={river_reach}", ["River Reach"], ras_data, -1)
        self.ras_data = reach_lines
        self.crs = crs
        self.river_reach = river_reach
        self.river = river_reach.split(",")[0].rstrip()
        self.reach = river_reach.split(",")[1].rstrip()
        self.units = units

        us_connection: str = None
        ds_connection: str = None

    @property
    def us_xs(self):
        """Upstream cross section."""
        return self.cross_sections[
            self.xs_gdf.loc[
                self.xs_gdf["river_station"] == self.xs_gdf["river_station"].max(),
                "river_reach_rs",
            ][0]
        ]

    @property
    def ds_xs(self):
        """Downstream cross section."""
        return self.cross_sections[
            self.xs_gdf.loc[
                self.xs_gdf["river_station"] == self.xs_gdf["river_station"].min(),
                "river_reach_rs",
            ][0]
        ]

    @property
    def number_of_cross_sections(self):
        """Number of cross sections."""
        return len(self.cross_sections)

    @property
    def number_of_coords(self):
        """Number of coordinates in reach."""
        return int(search_contents(self.ras_data, "Reach XY"))

    @property
    def coords(self):
        """Reach coordinates."""
        lines = text_block_from_start_str_length(
            f"Reach XY= {self.number_of_coords} ",
            math.ceil(self.number_of_coords / 2),
            self.ras_data,
        )
        return data_pairs_from_text_block(lines, 32)

    @property
    def reach_nodes(self):
        """Reach nodes."""
        return search_contents(self.ras_data, "Type RM Length L Ch R ", expect_one=False)

    @property
    @lru_cache
    def cross_sections(self):
        """Cross sections."""
        cross_sections, bridge_xs = [], []
        for header in self.reach_nodes:
            type, _, _, _, _ = header.split(",")[:5]

            if int(type) in [2, 3, 4]:
                bridge_xs = bridge_xs[:-2] + [4, 3]
            if int(type) != 1:
                continue
            if len(bridge_xs) == 0:
                bridge_xs = [0]
            else:
                bridge_xs.append(max([0, bridge_xs[-1] - 1]))
            xs_lines = text_block_from_start_end_str(
                f"Type RM Length L Ch R ={header}",
                ["Type RM Length L Ch R", "River Reach"],
                self.ras_data,
            )
            cross_sections.append(
                XS(xs_lines, self.river_reach, self.river, self.reach, self.crs, self.geom, self.units)
            )

        cross_sections = self.add_bridge_xs(cross_sections, bridge_xs)
        cross_sections = self.compute_multi_xs_variables(cross_sections)

        return cross_sections

    def add_bridge_xs(self, cross_sections, bridge_xs):
        """Add bridge cross sections attribute to the cross sections."""
        updated_xs = []
        for xs, br_xs in zip(cross_sections, bridge_xs):
            xs.set_bridge_xs(br_xs)
            updated_xs.append(xs)
        return updated_xs

    def compute_multi_xs_variables(self, cross_sections: list) -> dict:
        """Compute variables that depend on multiple cross sections.

        Set the thalweg drop, computed channel reach length and computed channel reach length
        ratio between a cross section and the cross section downstream.
        """
        ds_thalweg = cross_sections[-1].thalweg
        updated_xs = [cross_sections[-1]]
        for xs in cross_sections[::-1][1:]:
            xs.set_thalweg_drop(ds_thalweg)
            xs.set_computed_reach_length(updated_xs[-1].computed_river_station)
            xs.set_computed_reach_length_ratio()
            updated_xs.append(xs)
            ds_thalweg = xs.thalweg
        return {xs.river_reach_rs: xs for xs in updated_xs[::-1]}

    @property
    def structures(self):
        """Structures."""
        structures = {}
        for header in self.reach_nodes:
            type, _, _, _, _ = header.split(",")[:5]
            if int(type) == 1:
                xs_lines = text_block_from_start_end_str(
                    f"Type RM Length L Ch R ={header}",
                    ["Type RM Length L Ch R", "River Reach"],
                    self.ras_data,
                )
                cross_section = XS(xs_lines, self.river_reach, self.river, self.reach, self.crs, self.geom, self.units)
                continue
            elif int(type) in [2, 3, 4, 5, 6]:  # culvert or bridge or multiple openeing
                structure_lines = text_block_from_start_end_str(
                    f"Type RM Length L Ch R ={header}",
                    ["Type RM Length L Ch R", "River Reach"],
                    self.ras_data,
                )
            else:
                raise TypeError(
                    f"Unsupported structure type: {int(type)}. Supported structure types are 2, 3, 4, 5, and 6 corresponding to culvert, bridge, multiple openeing, inline structure, lateral structure, respectively"
                )

            structure = Structure(structure_lines, self.river_reach, self.river, self.reach, self.crs, cross_section)
            structures[structure.river_reach_rs] = structure

        return structures

    @property
    def geom(self):
        """Geometry of the reach."""
        return LineString(self.coords)

    @property
    @lru_cache
    def gdf(self):
        """Reach geodataframe."""
        return gpd.GeoDataFrame(
            {
                "geometry": [self.geom],
                "river": [self.river],
                "reach": [self.reach],
                "river_reach": [self.river_reach],
                # "number_of_coords": [self.number_of_coords],
                # "coords": [self.coords],
                "ras_data": ["\n".join(self.ras_data)],
            },
            crs=self.crs,
            geometry="geometry",
        )

    @property
    @lru_cache
    def xs_gdf(self):
        """Cross section geodataframe."""
        return pd.concat([xs.gdf for xs in self.cross_sections.values()])

    @property
    @lru_cache
    def structures_gdf(self):
        """Structures geodataframe."""
        return pd.concat([structure.gdf for structure in self.structures.values()])


class Junction:
    """HEC-RAS Junction."""

    def __init__(self, ras_data: List[str], junct: str, crs: str):
        self.crs = crs
        self.name = junct
        self.ras_data = text_block_from_start_str_to_empty_line(f"Junct Name={junct}", ras_data)

    def split_lines(self, lines: str, token: str, idx: int):
        """Split lines."""
        return list(map(lambda line: line.split(token)[idx].rstrip(), lines))

    @property
    def x(self):
        """Junction x coordinate."""
        return self.split_lines([search_contents(self.ras_data, "Junct X Y & Text X Y")], ",", 0)

    @property
    def y(self):
        """Junction y coordinate."""
        return self.split_lines([search_contents(self.ras_data, "Junct X Y & Text X Y")], ",", 1)

    @property
    def point(self):
        """Junction point."""
        return Point(self.x, self.y)

    @property
    def upstream_rivers(self):
        """Upstream rivers."""
        return ",".join(
            self.split_lines(
                search_contents(self.ras_data, "Up River,Reach", expect_one=False),
                ",",
                0,
            )
        )

    @property
    def downstream_rivers(self):
        """Downstream rivers."""
        return ",".join(
            self.split_lines(
                search_contents(self.ras_data, "Dn River,Reach", expect_one=False),
                ",",
                0,
            )
        )

    @property
    def upstream_reaches(self):
        """Upstream reaches."""
        return ",".join(
            self.split_lines(
                search_contents(self.ras_data, "Up River,Reach", expect_one=False),
                ",",
                1,
            )
        )

    @property
    def downstream_reaches(self):
        """Downstream reaches."""
        return ",".join(
            self.split_lines(
                search_contents(self.ras_data, "Dn River,Reach", expect_one=False),
                ",",
                1,
            )
        )

    @property
    def junction_lengths(self):
        """Junction lengths."""
        return ",".join(self.split_lines(search_contents(self.ras_data, "Junc L&A", expect_one=False), ",", 0))

    @property
    @lru_cache
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
            crs=self.crs,
        )
