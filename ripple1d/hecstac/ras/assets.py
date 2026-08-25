"""Asset instances of HEC-RAS model files."""

import os
import re
import shutil
import sqlite3
from functools import cached_property
from tempfile import NamedTemporaryFile

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.lines import Line2D
from pyproj import CRS
from pystac import MediaType
from shapely import MultiPolygon, Polygon

from ripple1d.hecstac.common.asset_factory import GenericAsset
from ripple1d.hecstac.common.consts import DEFAULT_CRS, HTTPS_PREFIX, S3_PREFIX
from ripple1d.hecstac.common.geometry import reproject_to_wgs84
from ripple1d.hecstac.common.s3_utils import make_uri_public, save_file_s3
from ripple1d.hecstac.ras.errors import Invalid1DGeometryError
from ripple1d.hecstac.ras.parser import (
    CachedFile,
    GeometryFile,
    GeometryHDFFile,
    PlanFile,
    PlanHDFFile,
    ProjectFile,
    QuasiUnsteadyFlowFile,
    RASHDFFile,
    SteadyFlowFile,
    UnsteadyFlowFile,
)
from ripple1d.hecstac.ras.utils import export_thumbnail, is_ras_prj

THUMBNAIL_CRS = "EPSG:4326"

CURRENT_PLAN = "HEC-RAS:current_plan"
PLAN_SHORT_ID = "HEC-RAS:plan_short_id"
TITLE = "HEC-RAS:title"
UNITS = "HEC-RAS:units"
VERSION = "HEC-RAS:version"
PROJECTION = "proj:wkt"

PLAN_FILE = "HEC-RAS:plan_file"
GEOMETRY_FILE = "HEC-RAS:geometry_file"
FLOW_FILE = "HEC-RAS:flow_file"

STEADY_FLOW_FILE = f"HEC-RAS:steady_{FLOW_FILE}"
QUASI_UNSTEADY_FLOW_FILE = f"HEC-RAS:quasi_unsteady_{FLOW_FILE}"
UNSTEADY_FLOW_FILE = f"HEC-RAS:unsteady_{FLOW_FILE}"


PLAN_FILES = f"{PLAN_FILE}s"
GEOMETRY_FILES = f"{GEOMETRY_FILE}s"
STEADY_FLOW_FILES = f"{STEADY_FLOW_FILE}s"
QUASI_UNSTEADY_FLOW_FILES = f"{QUASI_UNSTEADY_FLOW_FILE}s"
UNSTEADY_FLOW_FILES = f"{UNSTEADY_FLOW_FILE}s"

BREACH_LOCATIONS = "HEC-RAS:breach_locations"
RIVERS = "HEC-RAS:rivers"
REACHES = "HEC-RAS:reaches"
JUNCTIONS = "HEC-RAS:junctions"
CROSS_SECTIONS = "HEC-RAS:cross_sections"
STRUCTURES = "HEC-RAS:structures"
FLOW_ELEMENT_2D = "HEC-RAS:2d_flow_element"
CONNECTIONS = "HEC-RAS:connections"

HAS_2D = "HEC-RAS:has_2D_elements"
HAS_1D = "HEC-RAS:has_1D_elements"

N_PROFILES = "HEC-RAS:n_profiles"

BOUNDARY_LOCATIONS = "HEC-RAS:boundary_locations"
REFERENCE_LINES = "HEC-RAS:ref_lines"
REFERENCE_POINTS = "HEC-RAS:ref_points"
PRECIP_BC = "HEC-RAS:precip_bc"
IC_POINTS = "HEC-RAS:initial_condition_point_name"

PLAN_INFORMATION_BASE_OUTPUT_INTERVAL = "HEC-RAS:plan_information_base_output_interval"
PLAN_INFORMATION_COMPUTATION_TIME_STEP_BASE = "HEC-RAS:plan_information_computation_time_step_base"
PLAN_INFORMATION_FLOW_FILENAME = "HEC-RAS:plan_information_flow_filename"
PLAN_INFORMATION_GEOMETRY_FILENAME = "HEC-RAS:plan_information_geometry_filename"
PLAN_INFORMATION_PLAN_FILENAME = "HEC-RAS:plan_information_plan_filename"
PLAN_INFORMATION_PLAN_NAME = "HEC-RAS:plan_information_plan_name"
PLAN_INFORMATION_PROJECT_FILENAME = "HEC-RAS:plan_information_project_filename"
PLAN_INFORMATION_PROJECT_TITLE = "HEC-RAS:plan_information_project_title"
PLAN_INFORMATION_SIMULATION_END_TIME = "HEC-RAS:plan_information_simulation_end_time"
PLAN_INFORMATION_SIMULATION_START_TIME = "HEC-RAS:plan_information_simulation_start_time"
PLAN_PARAMETERS_1D_FLOW_TOLERANCE = "HEC-RAS:plan_parameters_1d_flow_tolerance"
PLAN_PARAMETERS_1D_MAXIMUM_ITERATIONS = "HEC-RAS:plan_parameters_1d_maximum_iterations"
PLAN_PARAMETERS_1D_MAXIMUM_ITERATIONS_WITHOUT_IMPROVEMENT = (
    "HEC-RAS:plan_parameters_1d_maximum_iterations_without_improvement"
)
PLAN_PARAMETERS_1D_MAXIMUM_WATER_SURFACE_ERROR_TO_ABORT = (
    "HEC-RAS:plan_parameters_1d_maximum_water_surface_error_to_abort"
)
PLAN_PARAMETERS_1D_STORAGE_AREA_ELEVATION_TOLERANCE = "HEC-RAS:plan_parameters_1d_storage_area_elevation_tolerance"
PLAN_PARAMETERS_1D_THETA = "HEC-RAS:plan_parameters_1d_theta"
PLAN_PARAMETERS_1D_THETA_WARMUP = "HEC-RAS:plan_parameters_1d_theta_warmup"
PLAN_PARAMETERS_1D_WATER_SURFACE_ELEVATION_TOLERANCE = "HEC-RAS:plan_parameters_1d_water_surface_elevation_tolerance"
PLAN_PARAMETERS_1D2D_GATE_FLOW_SUBMERGENCE_DECAY_EXPONENT = (
    "HEC-RAS:plan_parameters_1d2d_gate_flow_submergence_decay_exponent"
)
PLAN_PARAMETERS_1D2D_IS_STABLITY_FACTOR = "HEC-RAS:plan_parameters_1d2d_is_stablity_factor"
PLAN_PARAMETERS_1D2D_LS_STABLITY_FACTOR = "HEC-RAS:plan_parameters_1d2d_ls_stablity_factor"
PLAN_PARAMETERS_1D2D_MAXIMUM_NUMBER_OF_TIME_SLICES = "HEC-RAS:plan_parameters_1d2d_maximum_number_of_time_slices"
PLAN_PARAMETERS_1D2D_MINIMUM_TIME_STEP_FOR_SLICINGHOURS = (
    "HEC-RAS:plan_parameters_1d2d_minimum_time_step_for_slicinghours"
)
PLAN_PARAMETERS_1D2D_NUMBER_OF_WARMUP_STEPS = "HEC-RAS:plan_parameters_1d2d_number_of_warmup_steps"
PLAN_PARAMETERS_1D2D_WARMUP_TIME_STEP_HOURS = "HEC-RAS:plan_parameters_1d2d_warmup_time_step_hours"
PLAN_PARAMETERS_1D2D_WEIR_FLOW_SUBMERGENCE_DECAY_EXPONENT = (
    "HEC-RAS:plan_parameters_1d2d_weir_flow_submergence_decay_exponent"
)
PLAN_PARAMETERS_1D2D_MAXITER = "HEC-RAS:plan_parameters_1d2d_maxiter"
PLAN_PARAMETERS_2D_EQUATION_SET = "HEC-RAS:plan_parameters_2d_equation_set"
PLAN_PARAMETERS_2D_NAMES = "HEC-RAS:plan_parameters_2d_names"
PLAN_PARAMETERS_2D_VOLUME_TOLERANCE = "HEC-RAS:plan_parameters_2d_volume_tolerance"
PLAN_PARAMETERS_2D_WATER_SURFACE_TOLERANCE = "HEC-RAS:plan_parameters_2d_water_surface_tolerance"
METEOROLOGY_DSS_FILENAME = "HEC-RAS:meteorology_dss_filename"
METEOROLOGY_DSS_PATHNAME = "HEC-RAS:meteorology_dss_pathname"
METEOROLOGY_DATA_TYPE = "HEC-RAS:meteorology_data_type"
METEOROLOGY_MODE = "HEC-RAS:meteorology_mode"
METEOROLOGY_RASTER_CELLSIZE = "HEC-RAS:meteorology_raster_cellsize"
METEOROLOGY_SOURCE = "HEC-RAS:meteorology_source"
METEOROLOGY_UNITS = "HEC-RAS:meteorology_units"

PROJECT_FILE_NAME = "HEC-RAS:project_file_name"
GEOMETRY_TITLE = "HEC-RAS:geometry_title"
UNSTEADY_FLOW_TITLE = "HEC-RAS:unsteady_flow_title"
PLAN_TITLE = "HEC-RAS:plan_title"
FLOW_TITLE = "HEC-RAS:flow_title"

PRJ_CRS = "authority_code"


class ProjectAsset(GenericAsset[ProjectFile]):
    """HEC-RAS Project file asset."""

    regex_parse_str = r".+\.[pP][rR][jJ]$"
    __roles__ = ["ras-project"]
    __media_type__ = MediaType.TEXT
    __description__ = "The HEC-RAS project file."
    __file_class__ = ProjectFile

    @GenericAsset.extra_fields.getter
    def extra_fields(self) -> dict:
        """Return extra fields with added dynamic keys/values."""
        self._extra_fields[CURRENT_PLAN] = self.file.plan_current
        self._extra_fields[PLAN_FILES] = self.file.plan_files
        self._extra_fields[GEOMETRY_FILES] = self.file.geometry_files
        self._extra_fields[STEADY_FLOW_FILES] = self.file.steady_flow_files
        self._extra_fields[QUASI_UNSTEADY_FLOW_FILES] = self.file.quasi_unsteady_flow_files
        self._extra_fields[UNSTEADY_FLOW_FILES] = self.file.unsteady_flow_files
        return self._extra_fields


class ProjectionAsset(GenericAsset[CachedFile]):
    """.prj projection file."""

    regex_parse_str = r".+\.[pP][rR][jJ]$"
    __roles__ = ["projection"]
    __media_type__ = MediaType.TEXT
    __description__ = "A coordinate reference system projection file."
    __file_class__ = CachedFile

    @GenericAsset.extra_fields.getter
    def extra_fields(self) -> dict:
        """Return extra fields with added dynamic keys/values."""
        self._extra_fields[PRJ_CRS] = CRS.from_wkt(self.file.model_file.content).to_authority()


class PrjAsset(GenericAsset[CachedFile]):
    """Factory to create HEC-RAS project file assets or projection assets."""

    regex_parse_str = r".+\.[pP][rR][jJ]$"
    __roles__ = []
    __description__ = "N/A"
    __file_class__ = CachedFile

    @classmethod
    def from_dict(cls, data: dict) -> ProjectAsset | ProjectionAsset:
        """Subclass."""
        if is_ras_prj(data["href"]):
            return ProjectAsset.from_dict(data)
        else:
            return ProjectionAsset.from_dict(data)


class PlanAsset(GenericAsset[PlanFile]):
    """HEC-RAS Plan file asset."""

    regex_parse_str = r".+\.p\d{2}$"
    __roles__ = ["ras-plan"]
    __media_type__ = MediaType.TEXT
    __description__ = "The plan file which contains a list of associated input files and all simulation options."
    __file_class__ = PlanFile

    @GenericAsset.extra_fields.getter
    def extra_fields(self) -> dict:
        """Return extra fields with added dynamic keys/values."""
        self._extra_fields[PLAN_TITLE] = self.file.plan_title
        self._extra_fields[VERSION] = self.file.plan_version
        self._extra_fields[GEOMETRY_FILE] = self.file.geometry_file
        self._extra_fields[FLOW_FILE] = self.file.flow_file
        self._extra_fields[BREACH_LOCATIONS] = self.file.breach_locations
        self._extra_fields[PLAN_SHORT_ID] = self.file.short_identifier

        return self._extra_fields


class GeometryAsset(GenericAsset[GeometryFile]):
    """HEC-RAS Geometry file asset."""

    regex_parse_str = r".+\.g\d{2}$"
    __roles__ = ["ras-geometry"]
    __media_type__ = MediaType.TEXT
    __description__ = (
        "The geometry file which contains cross-sectional, 2D, hydraulic structures, and other geometric data."
    )
    __file_class__ = GeometryFile
    PROPERTIES_WITH_GDF = ["reaches", "junctions", "cross_sections", "structures"]

    @GenericAsset.extra_fields.getter
    def extra_fields(self) -> dict:
        """Return extra fields with added dynamic keys/values."""
        self._extra_fields[GEOMETRY_TITLE] = self.file.geom_title
        self._extra_fields[VERSION] = self.file.geom_version
        self._extra_fields[HAS_1D] = self.file.has_1d
        self._extra_fields[HAS_2D] = self.file.has_2d
        self._extra_fields[RIVERS] = list(self.file.rivers.keys())
        self._extra_fields[REACHES] = list(self.file.reaches.keys())
        self._extra_fields[JUNCTIONS] = list(self.file.junctions.keys())
        self._extra_fields[CROSS_SECTIONS] = list(self.file.cross_sections.keys())
        self._extra_fields[STRUCTURES] = list(self.file.structures.keys())
        self._extra_fields[FLOW_ELEMENT_2D] = list(self.file.storage_areas.keys())
        self._extra_fields[CONNECTIONS] = list(self.file.connections.keys())
        self._extra_fields[IC_POINTS] = self.file.ic_point_names
        self._extra_fields[REFERENCE_LINES] = self.file.ref_line_names
        self._extra_fields[REFERENCE_POINTS] = self.file.ref_point_names

        return self._extra_fields

    @cached_property
    def geometry(self) -> Polygon | MultiPolygon:
        """Retrieves concave hull of cross-sections."""
        return self.file.concave_hull

    @cached_property
    def has_1d(self) -> bool:
        """Check if geometry has any river centerlines."""
        return self.file.has_1d

    @cached_property
    def has_2d(self) -> bool:
        """Check if geometry has any 2D areas."""
        return self.file.has_2d

    @cached_property
    def geometry_wgs84(self) -> Polygon | MultiPolygon:
        """Reproject geometry to wgs84."""
        # TODO: this could be generalized to be a function that takes argument for CRS.
        if self.crs is None:
            return None
        elif self.geometry is None:
            return None
        else:
            return reproject_to_wgs84(self.geometry, self.crs)

    def _plot_river(self, ax: Axes):
        """Add the river centerline to a pyplot."""
        c = "#050dd5"
        self.file.reach_gdf.set_crs(self.crs).to_crs(THUMBNAIL_CRS).plot(ax=ax, color=c, label="River")
        legend_handle = [
            Line2D(
                [0],
                [0],
                color=c,
                linewidth=2,
                label="River",
            )
        ]
        return legend_handle

    def _plot_cross_sections(self, ax: Axes):
        """Add cross-sections to a pyplot."""
        c = "#5eeb34"
        self.file.xs_gdf.set_crs(self.crs).to_crs(THUMBNAIL_CRS).plot(ax=ax, color=c, label="XS")
        legend_handle = [
            Line2D(
                [0],
                [0],
                color=c,
                linewidth=2,
                label="XS",
            )
        ]
        return legend_handle

    def _plot_junctions(self, ax: Axes):
        """Add junctions to a pyplot."""
        c = "#eb344c"
        self.file.junction_gdf.set_crs(self.crs).to_crs(THUMBNAIL_CRS).plot(ax=ax, color=c, label="Junction")
        legend_handle = [
            Line2D(
                [0],
                [0],
                marker="o",
                color="none",
                markerfacecolor=c,
                markersize=8,
                label="Junction",
            )
        ]
        return legend_handle

    def _plot_structures(self, ax: Axes):
        """Add structures to a pyplot."""
        c = "k"
        self.file.structures_gdf.set_crs(self.crs).to_crs(THUMBNAIL_CRS).plot(ax=ax, color=c, label="Structure")
        legend_handle = [
            Line2D(
                [0],
                [0],
                color=c,
                linewidth=2,
                label="Structure",
            )
        ]
        return legend_handle

    def _add_thumbnail_asset(self, filepath: str) -> None:
        """Add the thumbnail image as an asset with a relative href."""
        if (
            not filepath.startswith(S3_PREFIX)
            and not filepath.startswith(HTTPS_PREFIX)
            and not os.path.exists(filepath)
        ):
            raise FileNotFoundError(f"Thumbnail file not found: {filepath}")

        asset = GenericAsset(
            href=filepath,
            title=filepath.split("/")[-1],
            description="Thumbnail image for the model",
        )
        asset.roles = ["thumbnail", "image/png"]
        return asset

    def _add_geopackage_asset(self, filepath: str) -> None:
        """Add the geometry geopackage as an asset with a relative href."""
        if (
            not filepath.startswith(S3_PREFIX)
            and not filepath.startswith(HTTPS_PREFIX)
            and not os.path.exists(filepath)
        ):
            raise FileNotFoundError(f"Geopackage file not found: {filepath}")

        asset = GenericAsset(
            href=filepath,
            title=filepath.split("/")[-1],
            description="GeoPackage file with geometry data extracted from .gxx file.",
        )
        asset.roles = ["RAS-GEOMETRY-GPKG", "APPLICATION/GEOPACKAGE+SQLITE3"]
        return asset

    def thumbnail(
        self, layers: list, title: str = "Model_Thumbnail", thumbnail_dest: str = None, make_public: bool = True
    ) -> str:
        """Create a thumbnail figure for a geometry file."""
        # Set up figure
        map_layers = []
        for layer in layers:
            if layer == "River":
                map_layers.append(self._plot_river)
            elif layer == "XS":
                map_layers.append(self._plot_cross_sections)
            elif layer == "Structure":
                map_layers.append(self._plot_structures)
            elif layer == "Junction":
                map_layers.append(self._plot_junctions)

        title = f"{title} - {os.path.basename(self.href)}"
        file_ext = os.path.basename(self.href).split(".")[-1]
        filename = f"thumbnail.{file_ext}.png"
        filepath = os.path.join(thumbnail_dest, filename)

        # Export
        export_thumbnail(map_layers, title, THUMBNAIL_CRS, filepath)

        # Add asset and return
        if make_public and filepath.startswith(S3_PREFIX):
            filepath = make_uri_public(filepath)
        return self._add_thumbnail_asset(filepath)

    def _attributed_xs_for_gpkg(self, model_meta: dict, flow_file: SteadyFlowFile = None) -> gpd.GeoDataFrame:
        """Load XS geometry and attribute for geopackage creation."""
        # River centerline
        xs_gdf = self.file.xs_gdf

        if flow_file is not None and flow_file.flow_change_locations is not None:
            xs_gdf[["flows", "profile_names"]] = None, None

            fcls = pd.DataFrame(flow_file.flow_change_locations)
            fcls["river_reach"] = fcls["river"] + fcls["reach"]

            for river_reach in fcls["river_reach"].unique():
                # get flow change locations for this reach
                fcls_rr = fcls.loc[fcls["river_reach"] == river_reach, :].sort_values(by="rs", ascending=False)

                # iterate through this reaches flow change locations and set cross section flows/profile names
                for _, row in fcls_rr.iterrows():
                    # add flows to xs_gdf
                    xs_gdf.loc[
                        (xs_gdf["river"] == row["river"])
                        & (xs_gdf["reach"] == row["reach"])
                        & (xs_gdf["river_station"] <= row["rs"]),
                        "flows",
                    ] = "\n".join([str(f) for f in row["flows"]])

                    # add profile names to xs_gdf
                    xs_gdf.loc[
                        (xs_gdf["river"] == row["river"])
                        & (xs_gdf["reach"] == row["reach"])
                        & (xs_gdf["river_station"] <= row["rs"]),
                        "profile_names",
                    ] = "\n".join(row["profile_names"])

        xs_gdf["flow_title"] = model_meta["primary_flow_title"]
        xs_gdf["plan_title"] = model_meta["primary_plan_title"]
        xs_gdf["geom_title"] = model_meta["primary_geom_title"]
        xs_gdf["version"] = model_meta["ras_version"]
        xs_gdf["units"] = model_meta["units"]
        xs_gdf["project_title"] = model_meta["ras_project_title"]

        return xs_gdf

    def geopackage(self, dst: str, model_meta: dict, flow_file: SteadyFlowFile = None, make_public: bool = True) -> str:
        """Make a geopackage for a geometry file."""
        # Validate that geometry is ready for gpkg creation
        n_reaches = len(self.file.reaches)
        n_cross_sections = len(self.file.cross_sections)
        if n_reaches == 0 or n_cross_sections == 0:
            raise Invalid1DGeometryError(f"{self.href} had {n_reaches} reaches and {n_cross_sections} cross-sections")

        # Define data
        filename = f"{self.name.split('.')[0]}.gpkg"
        filepath = os.path.join(dst, filename)
        layers = {
            "River": self.file.reach_gdf,
            "XS": self._attributed_xs_for_gpkg(model_meta, flow_file),
            "Junction": self.file.junction_gdf,
            "Structure": self.file.structures_gdf,
            "XS_concave_hull": self.file.concave_hull_gdf,
        }

        # Write spatial layers
        with NamedTemporaryFile(suffix=".gpkg") as f:
            for l in layers:
                if layers[l] is not None:
                    layers[l].set_crs(self.crs).to_file(f.name, layer=l, driver="GPKG")

            # Write non-spatial layer
            with sqlite3.connect(f.name) as con:
                cur = con.cursor()
                cur.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);")
                for k, v in model_meta.items():
                    cur.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?);", (k, v))
                con.commit()
            con.close()

            # Check if dst is on S3.  VSIS3 doesn't seem to allow sqlite writes via geopandas at the moment.
            # Need to make file locally then upload
            if filepath.lower().startswith(S3_PREFIX):
                save_file_s3(f.name, filepath)
            else:
                shutil.copy(f.name, filepath)

        if make_public and filepath.startswith(S3_PREFIX):
            filepath = make_uri_public(filepath)
        return self._add_geopackage_asset(filepath)


class SteadyFlowAsset(GenericAsset[SteadyFlowFile]):
    """HEC-RAS Steady Flow file asset."""

    regex_parse_str = r".+\.f\d{2}$"
    __roles__ = ["ras-steady"]
    __media_type__ = MediaType.TEXT
    __description__ = "Steady Flow file which contains profile information, flow data, and boundary conditions."
    __file_class__ = SteadyFlowFile

    @GenericAsset.extra_fields.getter
    def extra_fields(self) -> dict:
        """Return extra fields with added dynamic keys/values."""
        self._extra_fields[FLOW_TITLE] = self.file.flow_title
        self._extra_fields[N_PROFILES] = self.file.n_profiles
        return self._extra_fields


class QuasiUnsteadyFlowAsset(GenericAsset[QuasiUnsteadyFlowFile]):
    """HEC-RAS Quasi-Unsteady Flow file asset."""

    # TODO: implement this class

    regex_parse_str = r".+\.q\d{2}$"
    __roles__ = ["ras-quasi-unsteady"]
    __media_type__ = MediaType.TEXT
    __description__ = "Quasi-Unsteady Flow file."
    __file_class__ = QuasiUnsteadyFlowFile

    @GenericAsset.extra_fields.getter
    def extra_fields(self) -> dict:
        """Return extra fields with added dynamic keys/values."""
        self._extra_fields[TITLE] = self.file.flow_title
        return self._extra_fields


class UnsteadyFlowAsset(GenericAsset[UnsteadyFlowFile]):
    """HEC-RAS Unsteady Flow file asset."""

    regex_parse_str = r".+\.u\d{2}$"
    __roles__ = ["ras-unsteady"]
    __media_type__ = MediaType.TEXT
    __description__ = "The unsteady file contains hydrographs, initial conditions, and any flow options."
    __file_class__ = UnsteadyFlowFile

    @GenericAsset.extra_fields.getter
    def extra_fields(self) -> dict:
        """Return extra fields with added dynamic keys/values."""
        self._extra_fields[UNSTEADY_FLOW_TITLE] = self.file.flow_title
        self._extra_fields[BOUNDARY_LOCATIONS] = self.file.boundary_locations
        self._extra_fields[REFERENCE_LINES] = self.file.reference_lines
        self._extra_fields[PRECIP_BC] = self.file.precip_bc
        return self._extra_fields


class UnsteadyFlowHdfAsset(GenericAsset[RASHDFFile]):
    """HEC-RAS Unsteady Flow HDF file asset."""

    regex_parse_str = r".+\.u\d{2}\.hdf$"

    __roles__ = ["ras-unsteady"]
    __media_type__ = MediaType.HDF
    __description__ = "The HEC-RAS unsteady HDF file."
    __file_class__ = None

    @GenericAsset.extra_fields.getter
    def extra_fields(self) -> dict:
        """Return extra fields when complete."""
        pass


class PlanHdfAsset(GenericAsset[PlanHDFFile]):
    """HEC-RAS Plan HDF file asset."""

    regex_parse_str = r".+\.p\d{2}\.hdf$"
    __roles__ = ["ras-plan"]
    __media_type__ = MediaType.HDF
    __description__ = "The HEC-RAS plan HDF file."
    __file_class__ = PlanHDFFile

    @GenericAsset.extra_fields.getter
    def extra_fields(self) -> dict:
        """Return extra fields with added dynamic keys/values."""
        self._extra_fields[VERSION] = self.file.file_version
        self._extra_fields[UNITS] = self.file.units_system
        self._extra_fields[PLAN_INFORMATION_BASE_OUTPUT_INTERVAL] = self.file.plan_information_base_output_interval
        self._extra_fields[PLAN_INFORMATION_COMPUTATION_TIME_STEP_BASE] = (
            self.file.plan_information_computation_time_step_base
        )
        self._extra_fields[PLAN_INFORMATION_FLOW_FILENAME] = self.file.plan_information_flow_filename
        self._extra_fields[PLAN_INFORMATION_GEOMETRY_FILENAME] = self.file.plan_information_geometry_filename
        self._extra_fields[PLAN_INFORMATION_PLAN_FILENAME] = self.file.plan_information_plan_filename
        self._extra_fields[PLAN_INFORMATION_PLAN_NAME] = self.file.plan_information_plan_name
        self._extra_fields[PLAN_INFORMATION_PROJECT_FILENAME] = self.file.plan_information_project_filename
        self._extra_fields[PLAN_INFORMATION_PROJECT_TITLE] = self.file.plan_information_project_title
        self._extra_fields[PLAN_INFORMATION_SIMULATION_END_TIME] = self.file.plan_information_simulation_end_time
        self._extra_fields[PLAN_INFORMATION_SIMULATION_START_TIME] = self.file.plan_information_simulation_start_time
        self._extra_fields[PLAN_PARAMETERS_1D_FLOW_TOLERANCE] = self.file.plan_parameters_1d_flow_tolerance
        self._extra_fields[PLAN_PARAMETERS_1D_MAXIMUM_ITERATIONS] = self.file.plan_parameters_1d_maximum_iterations
        self._extra_fields[PLAN_PARAMETERS_1D_MAXIMUM_ITERATIONS_WITHOUT_IMPROVEMENT] = (
            self.file.plan_parameters_1d_maximum_iterations_without_improvement
        )
        self._extra_fields[PLAN_PARAMETERS_1D_MAXIMUM_WATER_SURFACE_ERROR_TO_ABORT] = (
            self.file.plan_parameters_1d_maximum_water_surface_error_to_abort
        )
        self._extra_fields[PLAN_PARAMETERS_1D_STORAGE_AREA_ELEVATION_TOLERANCE] = (
            self.file.plan_parameters_1d_storage_area_elevation_tolerance
        )
        self._extra_fields[PLAN_PARAMETERS_1D_THETA] = self.file.plan_parameters_1d_theta
        self._extra_fields[PLAN_PARAMETERS_1D_THETA_WARMUP] = self.file.plan_parameters_1d_theta_warmup
        self._extra_fields[PLAN_PARAMETERS_1D_WATER_SURFACE_ELEVATION_TOLERANCE] = (
            self.file.plan_parameters_1d_water_surface_elevation_tolerance
        )
        self._extra_fields[PLAN_PARAMETERS_1D2D_GATE_FLOW_SUBMERGENCE_DECAY_EXPONENT] = (
            self.file.plan_parameters_1d2d_gate_flow_submergence_decay_exponent
        )
        self._extra_fields[PLAN_PARAMETERS_1D2D_IS_STABLITY_FACTOR] = self.file.plan_parameters_1d2d_is_stablity_factor
        self._extra_fields[PLAN_PARAMETERS_1D2D_LS_STABLITY_FACTOR] = self.file.plan_parameters_1d2d_ls_stablity_factor
        self._extra_fields[PLAN_PARAMETERS_1D2D_MAXIMUM_NUMBER_OF_TIME_SLICES] = (
            self.file.plan_parameters_1d2d_maximum_number_of_time_slices
        )
        self._extra_fields[PLAN_PARAMETERS_1D2D_MINIMUM_TIME_STEP_FOR_SLICINGHOURS] = (
            self.file.plan_parameters_1d2d_minimum_time_step_for_slicinghours
        )
        self._extra_fields[PLAN_PARAMETERS_1D2D_NUMBER_OF_WARMUP_STEPS] = (
            self.file.plan_parameters_1d2d_number_of_warmup_steps
        )
        self._extra_fields[PLAN_PARAMETERS_1D2D_WARMUP_TIME_STEP_HOURS] = (
            self.file.plan_parameters_1d2d_warmup_time_step_hours
        )
        self._extra_fields[PLAN_PARAMETERS_1D2D_WEIR_FLOW_SUBMERGENCE_DECAY_EXPONENT] = (
            self.file.plan_parameters_1d2d_weir_flow_submergence_decay_exponent
        )
        self._extra_fields[PLAN_PARAMETERS_1D2D_MAXITER] = self.file.plan_parameters_1d2d_maxiter
        self._extra_fields[PLAN_PARAMETERS_2D_EQUATION_SET] = self.file.plan_parameters_2d_equation_set
        self._extra_fields[PLAN_PARAMETERS_2D_NAMES] = self.file.plan_parameters_2d_names
        self._extra_fields[PLAN_PARAMETERS_2D_VOLUME_TOLERANCE] = self.file.plan_parameters_2d_volume_tolerance
        self._extra_fields[PLAN_PARAMETERS_2D_WATER_SURFACE_TOLERANCE] = (
            self.file.plan_parameters_2d_water_surface_tolerance
        )
        self._extra_fields[METEOROLOGY_DSS_FILENAME] = self.file.meteorology_dss_filename
        self._extra_fields[METEOROLOGY_DSS_PATHNAME] = self.file.meteorology_dss_pathname
        self._extra_fields[METEOROLOGY_DATA_TYPE] = self.file.meteorology_data_type
        self._extra_fields[METEOROLOGY_MODE] = self.file.meteorology_mode
        self._extra_fields[METEOROLOGY_RASTER_CELLSIZE] = self.file.meteorology_raster_cellsize
        self._extra_fields[METEOROLOGY_SOURCE] = self.file.meteorology_source
        self._extra_fields[METEOROLOGY_UNITS] = self.file.meteorology_units
        return self._extra_fields


class GeometryHdfAsset(GenericAsset[GeometryHDFFile]):
    """HEC-RAS Geometry HDF file asset."""

    regex_parse_str = r".+\.g\d{2}\.hdf$"
    __roles__ = ["ras-geometry"]
    __media_type__ = MediaType.HDF
    __description__ = "The HEC-RAS geometry HDF file."
    __file_class__ = GeometryHDFFile

    @GenericAsset.extra_fields.getter
    def extra_fields(self) -> dict:
        """Return extra fields with added dynamic keys/values."""
        self._extra_fields[VERSION] = self.file.file_version
        self._extra_fields[UNITS] = self.file.units_system
        self._extra_fields[REFERENCE_LINES] = self.reference_line_names
        return self._extra_fields

    @cached_property
    def reference_line_names(self) -> list[str] | None:
        """Docstring."""  # TODO: fill out
        if self.file.reference_lines is not None and not self.file.reference_lines.empty:
            return list(self.file.reference_lines["refln_name"])

    def reference_lines_spatial(self, output_crs: str = DEFAULT_CRS) -> gpd.GeoDataFrame:
        """Return reference line names and geometry."""
        if self.file.reference_lines is not None and not self.file.reference_lines.empty:
            refln_gdf = self.file.reference_lines[["refln_name", "mesh_name", "geometry"]]
            if output_crs:
                refln_gdf = refln_gdf.to_crs(output_crs)
            return refln_gdf
        else:
            return None

    def reference_points_spatial(self, output_crs: str = DEFAULT_CRS) -> gpd.GeoDataFrame:
        """Return reference point names and geometry."""
        if self.file.reference_points is not None and not self.file.reference_points.empty:
            refpt_gdf = self.file.reference_points[["refpt_name", "mesh_name", "geometry"]]
            if output_crs:
                refpt_gdf = refpt_gdf.to_crs(output_crs)
            return refpt_gdf
        else:
            return None

    def bc_lines_spatial(self, output_crs: str = DEFAULT_CRS) -> gpd.GeoDataFrame:
        """Return boundary condition line names and geometry."""
        if self.file.bc_lines is not None and not self.file.bc_lines.empty:
            bc_line_gdf = self.file.bc_lines[["name", "mesh_name", "geometry"]]
            if output_crs:
                bc_line_gdf = bc_line_gdf.to_crs(output_crs)
            return bc_line_gdf
        else:
            return None

    def model_perimeter(self, output_crs: str = DEFAULT_CRS) -> gpd.GeoDataFrame:
        """Return model perimeter from mesh areas."""
        mesh_areas = self.file.mesh_areas()
        if mesh_areas:
            perimeter_gdf = gpd.GeoDataFrame(geometry=[mesh_areas], crs=self.crs)
            if output_crs:
                perimeter_gdf = perimeter_gdf.to_crs(output_crs)
            return perimeter_gdf
        else:
            return None

    @cached_property
    def has_2d(self) -> bool:
        """Check if the geometry asset has 2d geometry."""
        try:
            if self.file.mesh_areas():
                return True
        except ValueError:
            return False

    @cached_property
    def has_1d(self) -> bool:
        """Check if the geometry asset has 1d geometry."""
        return len(self.file.cross_sections) > 0

    @cached_property
    def geometry(self) -> Polygon | MultiPolygon:
        """Retrieves concave hull of cross-sections."""
        return self.file.mesh_areas(self.crs)

    @cached_property
    def geometry_wgs84(self) -> Polygon | MultiPolygon:
        """Reproject geometry to wgs84."""
        # TODO: this could be generalized to be a function that takes argument for CRS.
        if self.crs is None:
            return None
        else:
            return reproject_to_wgs84(self.geometry, self.crs)

    def _plot_mesh_areas(self, ax: Axes) -> list[Line2D]:
        """Plot mesh areas on the given axes."""
        self.file.mesh_cells.set_crs(self.crs).to_crs(THUMBNAIL_CRS).plot(
            ax=ax,
            edgecolor="silver",
            facecolor="none",
            linestyle="-",
            alpha=0.7,
            label="Mesh Polygons",
        )
        legend_handle = [
            Line2D(
                [0],
                [0],
                color="silver",
                linestyle="-",
                linewidth=2,
                label="Mesh Polygons",
            )
        ]
        return legend_handle

    def _plot_breaklines(self, ax: Axes) -> list[Line2D]:
        """Plot breaklines on the given axes."""
        self.file.breaklines.set_crs(self.crs).to_crs(THUMBNAIL_CRS).plot(
            ax=ax, edgecolor="black", linestyle="-", alpha=0.3, label="Breaklines"
        )
        legend_handle = [
            Line2D(
                [0],
                [0],
                color="black",
                linestyle="-",
                alpha=0.4,
                linewidth=2,
                label="Breaklines",
            )
        ]
        return legend_handle

    def _plot_bc_lines(self, ax: Axes) -> list[Line2D]:
        """Plot boundary condition lines on the given axes."""
        bc_lines = self.file.bc_lines.set_crs(self.crs).to_crs(THUMBNAIL_CRS)
        legend_handles = [
            Line2D([0], [0], color="none", linestyle="None", label="BC Lines"),
        ]
        colors = plt.get_cmap("Dark2", len(bc_lines))

        for bc_line, color in zip(bc_lines.itertuples(), colors.colors):
            x_coords, y_coords = bc_line.geometry.xy
            ax.plot(
                x_coords,
                y_coords,
                color=color,
                linestyle="-",
                linewidth=6,
                label=bc_line.name,
            )
            legend_handles.append(
                Line2D(
                    [0],
                    [0],
                    color=color,
                    linestyle="-",
                    linewidth=2,
                    label=bc_line.name,
                )
            )
        return legend_handles

    def _add_thumbnail_asset(self, filepath: str) -> None:
        """Add the thumbnail image as an asset with a relative href."""
        if not (filepath.startswith(S3_PREFIX) or filepath.startswith(HTTPS_PREFIX)) and not os.path.exists(filepath):
            raise FileNotFoundError(f"Thumbnail file not found: {filepath}")

        asset = GenericAsset(
            href=filepath,
            title=filepath.split("/")[-1],
            description="Thumbnail image for the model",
        )
        asset.roles = ["thumbnail"]
        asset.media_type = "image/png"
        return asset

    def thumbnail(
        self, layers: list, title: str = "Model_Thumbnail", thumbnail_dest: str = None, make_public: bool = True
    ):
        """
        Create a thumbnail figure for a geometry hdf file, includingvarious geospatial layers such as USGS gages, mesh areas, breaklines, and boundary condition (BC) lines.

        Parameters
        ----------
        layers : list
            A list of model layers to include in the thumbnail plot.
            Options include "usgs_gages", "mesh_areas", "breaklines", and "bc_lines".
        title : str, optional
            Title of the figure, by default "Model Thumbnail".
        thumbnail_dest : str, optional
            Directory for created thumbnails. If None then thumbnails will be exported to same level as the item.
        """
        map_layers = []
        for layer in layers:
            if layer == "mesh_areas":
                map_layers.append(self._plot_mesh_areas)
            elif layer == "breaklines":
                map_layers.append(self._plot_breaklines)
            elif layer == "bc_lines":
                map_layers.append(self._plot_bc_lines)
            # TODO: Add support for river centerline and cross-sections (from .hdf)
        title = f"{title} - {os.path.basename(self.href)}"
        hdf_ext = os.path.basename(self.href).split(".")[-2]
        filename = f"thumbnail.{hdf_ext}.png"
        filepath = os.path.join(thumbnail_dest, filename)

        # Export
        export_thumbnail(map_layers, title, THUMBNAIL_CRS, filepath)

        # Add asset and return
        if make_public and filepath.startswith(S3_PREFIX):
            filepath = make_uri_public(filepath)
        return self._add_thumbnail_asset(filepath)


class RunFileAsset(GenericAsset):
    """Run file asset for steady flow analysis."""

    regex_parse_str = r".+\.r\d{2}$"
    __roles__ = ["ras-run"]
    __media_type__ = MediaType.TEXT
    __description__ = "Run file for steady flow analysis which contains all the necessary input data required for the RAS computational engine."
    __file_class__ = None


class ComputationalLevelOutputAsset(GenericAsset):
    """Computational Level Output asset."""

    regex_parse_str = r".+\.hyd\d{2}$"
    __roles__ = ["ras-computational-level-output"]
    __media_type__ = MediaType.TEXT
    __description__ = "Detailed Computational Level output file."
    __file_class__ = None


class GeometricPreprocessorAsset(GenericAsset):
    """Geometric Pre-Processor asset."""

    regex_parse_str = r".+\.c\d{2}$"
    __roles__ = ["ras-geometric-preprocessor"]
    __media_type__ = MediaType.TEXT
    __description__ = "Geometric Pre-Processor output file containing hydraulic properties, rating curves, and more."
    __file_class__ = None  # TODO:  make a generic parent for these.


class BoundaryConditionAsset(GenericAsset):
    """Boundary Condition asset."""

    regex_parse_str = r".+\.b\d{2}$"
    __roles__ = ["ras-boundary-condition"]
    __media_type__ = MediaType.TEXT
    __description__ = "Boundary Condition file."
    __file_class__ = None


class UnsteadyFlowLogAsset(GenericAsset):
    """Unsteady Flow Log asset."""

    regex_parse_str = r".+\.bco\d{2}$"
    __roles__ = ["ras-unsteady-log"]
    __media_type__ = MediaType.TEXT
    __description__ = "Unsteady Flow Log output file."
    __file_class__ = None


class SedimentDataAsset(GenericAsset):
    """Sediment Data asset."""

    regex_parse_str = r".+\.s\d{2}$"
    __roles__ = ["ras-sediment-data"]
    __media_type__ = MediaType.TEXT
    __description__ = "Sediment data file containing flow data, boundary conditions, and sediment data."
    __file_class__ = None


class HydraulicDesignAsset(GenericAsset):
    """Hydraulic Design asset."""

    regex_parse_str = r".+\.h\d{2}$"
    __roles__ = ["ras-hydraulic-design"]
    __media_type__ = MediaType.TEXT
    __description__ = "Hydraulic Design data file."
    __file_class__ = None


class WaterQualityAsset(GenericAsset):
    """Water Quality asset."""

    regex_parse_str = r".+\.w\d{2}$"
    __roles__ = ["ras-water-quality"]
    __media_type__ = MediaType.TEXT
    __description__ = "Water Quality file containing temperature boundary conditions and meteorological data."
    __file_class__ = None


class SedimentTransportCapacityAsset(GenericAsset):
    """Sediment Transport Capacity asset."""

    regex_parse_str = r".+\.SedCap\d{2}$"
    __roles__ = ["ras-sediment-transport-capacity"]
    __media_type__ = MediaType.TEXT
    __description__ = "Sediment Transport Capacity data."
    __file_class__ = None


class XSOutputAsset(GenericAsset):
    """Cross Section Output asset."""

    regex_parse_str = r".+\.SedXS\d{2}$"
    __roles__ = ["ras-xs-output"]
    __media_type__ = MediaType.TEXT
    __description__ = "Cross section output file."
    __file_class__ = None


class XSOutputHeaderAsset(GenericAsset):
    """Cross Section Output Header asset."""

    regex_parse_str = r".+\.SedHeadXS\d{2}$"
    __roles__ = ["ras-xs-output-header"]
    __media_type__ = MediaType.TEXT
    __description__ = "Header file for the cross section output."
    __file_class__ = None


class WaterQualityRestartAsset(GenericAsset):
    """Water Quality Restart asset."""

    regex_parse_str = r".+\.wqrst\d{2}$"
    __roles__ = ["ras-water-quality-restart"]
    __media_type__ = MediaType.TEXT
    __description__ = "The water quality restart file."
    __file_class__ = None


class SedimentOutputAsset(GenericAsset):
    """Sediment Output asset."""

    regex_parse_str = r".+\.sed$"
    __roles__ = ["ras-sediment-output"]
    __media_type__ = MediaType.TEXT
    __description__ = "Detailed sediment output file."
    __file_class__ = None


class BinaryLogAsset(GenericAsset):
    """Binary Log asset."""

    regex_parse_str = r".+\.blf$"
    __roles__ = ["ras-binary-log"]
    __media_type__ = MediaType.TEXT
    __description__ = "Binary Log file."
    __file_class__ = None


class DSSAsset(GenericAsset):
    """DSS asset."""

    regex_parse_str = r".+\.dss$"
    __roles__ = ["HEC-DSS"]
    __media_type__ = "x-dss"
    __description__ = "The DSS file contains results and other simulation information."
    __file_class__ = None


class LogAsset(GenericAsset):
    """Log asset."""

    regex_parse_str = r".+\.log$"
    __roles__ = ["ras-log"]
    __media_type__ = MediaType.TEXT
    __description__ = "The log file contains information related to simulation processes."
    __file_class__ = None


class RestartAsset(GenericAsset):
    """Restart file asset."""

    regex_parse_str = r".+\.rst$"
    __roles__ = ["ras-restart"]
    __media_type__ = MediaType.TEXT
    __description__ = "Restart file for resuming simulation runs."
    __file_class__ = None


class SiamInputAsset(GenericAsset):
    """SIAM Input Data file asset."""

    regex_parse_str = r".+\.SiamInput$"
    __roles__ = ["ras-siam-input"]
    __media_type__ = MediaType.TEXT
    __description__ = "SIAM Input Data file."
    __file_class__ = None


class SiamOutputAsset(GenericAsset):
    """SIAM Output Data file asset."""

    regex_parse_str = r".+\.SiamOutput$"
    __roles__ = ["ras-siam-output"]
    __media_type__ = MediaType.TEXT
    __description__ = "SIAM Output Data file."
    __file_class__ = None


class WaterQualityLogAsset(GenericAsset):
    """Water Quality Log file asset."""

    regex_parse_str = r".+\.bco$"
    __roles__ = ["ras-water-quality-log"]
    __media_type__ = MediaType.TEXT
    __description__ = "Water quality log file."
    __file_class__ = None


class ColorScalesAsset(GenericAsset):
    """Color Scales file asset."""

    regex_parse_str = r".+\.color-scales$"
    __roles__ = ["ras-color-scales"]
    __media_type__ = MediaType.TEXT
    __description__ = "File that contains the water quality color scale."
    __file_class__ = None


class ComputationalMessageAsset(GenericAsset):
    """Computational Message file asset."""

    regex_parse_str = r".+\.comp-msgs.txt$"
    __roles__ = ["ras-computational-message"]
    __media_type__ = MediaType.TEXT
    __description__ = "Computational Message text file which contains messages from the computation process."
    __file_class__ = None


class UnsteadyRunFileAsset(GenericAsset):
    """Run file for Unsteady Flow asset."""

    regex_parse_str = r".+\.x\d{2}$"
    __roles__ = ["ras-run"]
    __media_type__ = MediaType.TEXT
    __description__ = "Run file for Unsteady Flow simulations."
    __file_class__ = None


class OutputFileAsset(GenericAsset):
    """Output RAS file asset."""

    regex_parse_str = r".+\.o\d{2}$"
    __roles__ = ["ras-output"]
    __media_type__ = MediaType.TEXT
    __description__ = "Output RAS file which contains all computed results."
    __file_class__ = None


class InitialConditionsFileAsset(GenericAsset):
    """Initial Conditions file asset."""

    regex_parse_str = r".+\.IC\.O\d{2}$"
    __roles__ = ["ras-initial-conditions"]
    __media_type__ = MediaType.TEXT
    __description__ = "Initial conditions file for unsteady flow plan."
    __file_class__ = None


class PlanRestartFileAsset(GenericAsset):
    """Restart file for Unsteady Flow Plan asset."""

    regex_parse_str = r".+\.p\d{2}\.rst$"
    __roles__ = ["ras-restart"]
    __media_type__ = MediaType.TEXT
    __description__ = "Restart file for unsteady flow plan."
    __file_class__ = None


class RasMapperFileAsset(GenericAsset):
    """RAS Mapper file asset."""

    regex_parse_str = r".+\.rasmap$"
    __roles__ = ["ras-ras-mapper"]
    __media_type__ = MediaType.TEXT
    __description__ = "RAS Mapper file."
    __file_class__ = None


class RasMapperBackupFileAsset(GenericAsset):
    """Backup RAS Mapper file asset."""

    regex_parse_str = r".+\.rasmap\.backup$"
    __roles__ = ["ras-mapper"]
    __media_type__ = MediaType.TEXT
    __description__ = "Backup RAS Mapper file."
    __file_class__ = None


class RasMapperOriginalFileAsset(GenericAsset):
    """Original RAS Mapper file asset."""

    regex_parse_str = r".+\.rasmap\.original$"
    __roles__ = ["ras-mapper"]
    __media_type__ = MediaType.TEXT
    __description__ = "Original RAS Mapper file."
    __file_class__ = None


class MiscTextFileAsset(GenericAsset):
    """Miscellaneous Text file asset."""

    regex_parse_str = r".+\.txt$"
    __roles__ = [MediaType.TEXT]
    __description__ = "Miscellaneous text file."
    __file_class__ = None


class MiscXMLFileAsset(GenericAsset):
    """Miscellaneous XML file asset."""

    regex_parse_str = r".+\.xml$"
    __roles__ = [MediaType.XML]
    __description__ = "Miscellaneous XML file."
    __file_class__ = None


RAS_ASSET_CLASSES = [
    PrjAsset,
    PlanAsset,
    GeometryAsset,
    SteadyFlowAsset,
    QuasiUnsteadyFlowAsset,
    UnsteadyFlowAsset,
    UnsteadyFlowHdfAsset,
    PlanHdfAsset,
    GeometryHdfAsset,
    RunFileAsset,
    ComputationalLevelOutputAsset,
    GeometricPreprocessorAsset,
    BoundaryConditionAsset,
    UnsteadyFlowLogAsset,
    SedimentDataAsset,
    HydraulicDesignAsset,
    WaterQualityAsset,
    SedimentTransportCapacityAsset,
    XSOutputAsset,
    XSOutputHeaderAsset,
    WaterQualityRestartAsset,
    SedimentOutputAsset,
    BinaryLogAsset,
    DSSAsset,
    LogAsset,
    RestartAsset,
    SiamInputAsset,
    SiamOutputAsset,
    WaterQualityLogAsset,
    ColorScalesAsset,
    ComputationalMessageAsset,
    UnsteadyRunFileAsset,
    OutputFileAsset,
    InitialConditionsFileAsset,
    PlanRestartFileAsset,
    RasMapperFileAsset,
    RasMapperBackupFileAsset,
    RasMapperOriginalFileAsset,
    MiscTextFileAsset,
    MiscXMLFileAsset,
]

RAS_EXTENSION_MAPPING = {re.compile(cls.regex_parse_str, re.IGNORECASE): cls for cls in RAS_ASSET_CLASSES}
