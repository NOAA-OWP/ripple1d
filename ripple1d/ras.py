"""RAS Class and functions."""

import glob
import json
import logging
import os
import platform
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import List

import fiona
import geopandas as gpd
import h5py
import pandas as pd
from pyproj import CRS

from ripple1d.consts import (
    DEFAULT_ND_SLOPE,
    FLOW_HDF_PATH,
    PROFILE_NAMES_HDF_PATH,
    SHOW_RAS,
    SUPPORTED_LAYERS,
    TERRAIN_NAME,
    TERRAIN_PATH,
    WSE_HDF_PATH,
    XS_NAMES_HDF_PATH,
)
from ripple1d.data_model import FlowChangeLocation, Junction, Reach
from ripple1d.errors import (
    FlowTitleAlreadyExistsError,
    HECRASVersionNotInstalledError,
    InvalidStructureDataError,
    NoCrossSectionLayerError,
    NoFlowFileSpecifiedError,
    NoGeometryFileSpecifiedError,
    NoRiverLayerError,
    PlanTitleAlreadyExistsError,
)

# ToManyPlansError,
from ripple1d.rasmap import PLAN, RASMAP_631, TERRAIN
from ripple1d.utils.dg_utils import get_terrain_exe_path
from ripple1d.utils.ripple_utils import (
    decode,
    replace_line_in_contents,
    search_contents,
    text_block_from_start_end_str,
)

RAS_FILE_TYPES = ["Plan", "Flow", "Geometry", "Project"]

VALID_PLANS = [f".p{i:02d}" for i in range(1, 100)] + [f".P{i:02d}" for i in range(1, 100)]
VALID_GEOMS = [f".g{i:02d}" for i in range(1, 100)] + [f".G{i:02d}" for i in range(1, 100)]
VALID_STEADY_FLOWS = [f".f{i:02d}" for i in range(1, 100)] + [f".F{i:02d}" for i in range(1, 100)]
VALID_UNSTEADY_FLOWS = [f".u{i:02d}" for i in range(1, 100)] + [f".U{i:02d}" for i in range(1, 100)]
VALID_QUASISTEADY_FLOWS = [f".q{i:02d}" for i in range(1, 100)] + [f".Q{i:02d}" for i in range(1, 100)]


# Decorator Functions
def check_crs(func):
    """Check CRS decorator."""

    def wrapper(self, *args, **kwargs):
        if self.crs is None:
            raise ValueError("Projection cannot be None")
        return func(self, *args, **kwargs)

    return wrapper


def combine_root_extension(func):
    """Combine root extension decorator."""

    def wrapper(self, *args, **kwargs):
        extensions = func(self, *args, **kwargs)
        if isinstance(extensions, list):
            return [
                self._ras_root_path + "." + extension.replace(" ", "").lstrip(".").lower() for extension in extensions
            ]
        else:
            return self._ras_root_path + "." + extensions.replace(" ", "").lstrip(".").lower()

    return wrapper


def check_version_installed(version: str):
    """Check version installed decorator."""

    def decorator(func):
        def wrapper(self, *args, **kwargs):
            if not os.path.exists("C:\\Program Files (x86)\\HEC\\HEC-RAS\\6.3.1\\Ras.exe"):
                raise HECRASVersionNotInstalledError(
                    f"Could not find the specified RAS version; please ensure it is installed. Version provided: {version}."
                )
            return func(self, *args, **kwargs)

        return wrapper

    return decorator


def check_windows(func):
    """Check windows decorator."""

    def wrapper(self, *args, **kwargs):
        if platform.system() != "Windows":
            raise SystemError("This method can only be run on a Windows machine.")
        return func(self, *args, **kwargs)

    return wrapper


# classes
class RasManager:
    """Manage HEC-RAS projects."""

    def __init__(
        self,
        ras_text_file_path: str,
        version: str = "631",
        terrain_path: str = None,
        crs: CRS = None,
        new_project: bool = False,
    ):
        self.version = version
        self.terrain_path = terrain_path
        self.ras_project = RasProject(ras_text_file_path, new_file=new_project)

        self.crs = CRS(crs)
        self.plans = self.get_plans()
        self.geoms = self.get_geoms()
        self.flows = self.get_flows()
        self.plan = self.current_plan

    def __repr__(self):
        """Representation of the RasManager class."""
        return f"RasManager(project={self.ras_project._ras_text_file_path} ras-version={self.version})"

    @classmethod
    def from_gpkg(
        cls,
        ras_project_text_file: str,
        nwm_id,
        ras_gpkg_file_path: str,
        version: str = "631",
        terrain_path: str = None,
    ):
        """Create a new RasManager object from a geopackage."""
        inst = cls(
            ras_project_text_file,
            version,
            terrain_path=terrain_path,
            crs=gpd.read_file(ras_gpkg_file_path, layer="XS").crs,
            new_project=True,
        )

        inst.new_geom_from_gpkg(ras_gpkg_file_path, nwm_id)
        inst.ras_project.write_contents()
        return inst

    @property
    def current_plan(self):
        """Get the current plan."""
        for plan in self.plans.values():
            if plan.file_extension == self.ras_project.current_plan:
                return plan

    @property
    def projection_file(self):
        """Write the current projection to file and return the file path."""
        projection_file = os.path.join(self.ras_project._ras_dir, "crs.prj")
        with open(projection_file, "w") as f:
            f.write(self.crs.to_wkt("WKT1_ESRI"))
        return projection_file

    def get_plans(self):
        """Create plan objects for each plan."""
        plans = {}
        for plan_file in self.ras_project.plans:
            try:
                plan = RasPlanText(plan_file, self.crs, units=self.ras_project.units)
                plans[plan.title] = plan
            except FileNotFoundError:
                logging.info(f"Could not find plan file: {plan_file}")
        return plans

    @check_crs
    def get_geoms(self):
        """Create geom objects for each geom."""
        geoms = {}
        for geom_file in self.ras_project.geoms:
            try:
                geom = RasGeomText(geom_file, self.crs, units=self.ras_project.units)
                geoms[geom.title] = geom
            except FileNotFoundError:
                logging.warning(f"Could not find geom file: {geom_file}")
        return geoms

    def get_flows(self):
        """Create flow objects for each flow."""
        flows = {}
        for flow_file in self.ras_project.steady_flows:
            try:
                flow = RasFlowText(flow_file)
                flows[flow.title] = flow
            except FileNotFoundError:
                logging.warning(f"Could not find flow file: {flow_file}")
        return flows

    def normal_depth_run(
        self,
        plan_flow_title: str,
        geom_title: str,
        flow_change_locations: list[FlowChangeLocation],
        profile_names: list[str],
        normal_depth: float = DEFAULT_ND_SLOPE,
        write_depth_grids: bool = False,
        show_ras: bool = SHOW_RAS,
        run_ras: bool = True,
        flow_file_description: str = "",
    ):
        """Create a new normal depth run."""
        if plan_flow_title in self.flows.keys():
            raise FlowTitleAlreadyExistsError(f"The specified flow title {plan_flow_title} already exists")

        # get a new extension number for the new flow file
        new_extension_number = get_new_extension_number(self.flows)
        flow_text_file = self.ras_project._ras_root_path + f".f{new_extension_number}"

        # create new flow
        rft = RasFlowText(flow_text_file, new_file=True)

        # write headers
        rft.contents += rft.write_headers(plan_flow_title, profile_names, flow_file_description)

        for fcl in flow_change_locations:
            # write discharges
            rft.contents += rft.write_discharges(fcl.flows, fcl.river, fcl.reach, fcl.rs)

        for fcl in flow_change_locations:
            # write normal depth
            rft.contents += rft.write_ds_normal_depth(len(fcl.flows), normal_depth, fcl.river, fcl.reach)

        # write flow file content
        rft.write_contents()

        # add new flow to the ras class
        self.flows[plan_flow_title] = rft
        self.flow = rft

        # add to ras project contents
        self.ras_project.contents.append(f"Flow File=f{new_extension_number}")

        return self.write_new_plan_text_file(plan_flow_title, geom_title, write_depth_grids, show_ras, run_ras)

    def kwses_run(
        self,
        plan_flow_title: str,
        geom_title: str,
        depths: List[float],
        wses: List[float],
        flows: List[float],
        river: str,
        reach: str,
        us_river_station: float,
        write_depth_grids: bool = False,
        show_ras: bool = SHOW_RAS,
        run_ras: bool = True,
    ):
        """Create a new known water surface elevation run."""
        if plan_flow_title in self.flows.keys():
            raise FlowTitleAlreadyExistsError(f"The specified flow title {plan_flow_title} already exists")

        # get a new extension number for the new flow file
        new_extension_number = get_new_extension_number(self.flows)
        flow_text_file = self.ras_project._ras_root_path + f".f{new_extension_number}"

        # create new flow
        rft = RasFlowText(flow_text_file, new_file=True)

        profile_names = [f"f_{flow}-z_{str(wse).replace('.','_')}" for flow, wse in zip(flows, wses)]

        profile_name_map = {str(key): str(val) for key, val in zip(range(len(profile_names)), profile_names)}

        description = json.dumps(profile_name_map)

        # write headers
        rft.contents += rft.write_headers(plan_flow_title, [i for i in profile_name_map.keys()], description)

        # write discharges
        rft.contents += rft.write_discharges(flows, river, reach, us_river_station)

        # write DS boundary conditions
        rft.contents += rft.write_ds_known_wse(wses, river, reach)

        # write flow file content
        rft.write_contents()

        # add new flow to the ras class
        self.flows[plan_flow_title] = rft
        self.flow = rft

        # add to ras project contents
        self.ras_project.contents.append(f"Flow File=f{new_extension_number}")

        return self.write_new_plan_text_file(plan_flow_title, geom_title, write_depth_grids, show_ras, run_ras)

    def new_geom_from_gpkg(
        self,
        ras_gpkg_file_path: str,
        title: str,
    ):
        """Create a new geometry file from a geopackage."""
        new_extension_number = get_new_extension_number(self.geoms)
        text_file = self.ras_project._ras_root_path + f".g{new_extension_number}"
        geom_text_file = RasGeomText.from_gpkg(ras_gpkg_file_path, title, self.version, text_file)
        geom_text_file.write_contents()
        self.geoms[geom_text_file.title] = geom_text_file
        self.ras_project.contents.append(f"Geom File=g{new_extension_number}")

    def update_rasmapper_for_mapping(self):
        """Write a rasmapper file to output depth grids for the current plan."""
        # manage rasmapper
        map_file = f"{self.ras_project._ras_root_path}.rasmap"

        if os.path.exists(map_file):
            os.remove(map_file)

        if os.path.exists(map_file + ".backup"):
            os.remove(map_file + ".backup")

        terrain_relative_path = os.path.relpath(self.terrain_path, self.ras_project._ras_dir)
        terrain_name = os.path.splitext(os.path.basename(self.terrain_path))[0]

        rasmap = RasMap(map_file, self.plan.geom, self.version)
        rasmap.update_crs(self.projection_file)
        rasmap.add_terrain(terrain_name, terrain_relative_path)
        rasmap.add_plan_layer(
            self.plan.title,
            os.path.basename(self.plan.hdf_file),
            self.plan.flow.profile_names,
        )
        rasmap.add_result_layers(self.plan.title, self.plan.flow.profile_names, "Depth")
        rasmap.write()

    @check_windows
    @check_version_installed("631")
    def write_new_plan_text_file(
        self, plan_flow_title, geom_title, write_depth_grids: bool = False, show_ras=SHOW_RAS, run_ras=True
    ):
        """Write new plan text file decorator."""
        if plan_flow_title in self.plans.keys():
            raise PlanTitleAlreadyExistsError(f"The specified plan title {plan_flow_title} already exists")

        if plan_flow_title not in self.flows.keys():
            raise ValueError(f"The specified flow title {plan_flow_title} does not exist")

        if geom_title not in self.geoms.keys():
            raise ValueError(f"The specified geom title {geom_title} does not exist")

        # get a new extension number for the new plan
        new_extension_number = get_new_extension_number(self.plans)

        plan_text_file = self.ras_project._ras_root_path + f".p{new_extension_number}"

        # create plan
        rpt = RasPlanText(plan_text_file, self.crs, new_file=True, units=self.ras_project.units)

        # populate new plan info
        rpt.new_plan_contents(
            plan_flow_title,
            plan_flow_title,
            self.flows[plan_flow_title],
            self.geoms[geom_title],
            write_depth_grids,
        )

        # write content
        rpt.write_contents()

        # add new plan to the ras class
        self.plans[plan_flow_title] = rpt
        self.plan = rpt

        # add to ras project contents
        self.ras_project.contents.append(f"Plan File=p{new_extension_number}")

        # update the content of the RAS project file
        self.contents = self.ras_project.set_current_plan(self.plans[plan_flow_title].file_extension)

        # write the update RAS project file content
        self.ras_project.write_updated_contents()

        if write_depth_grids:
            self.update_rasmapper_for_mapping()

        if run_ras:
            runRAS = f'C:\\Program Files (x86)\\HEC\\HEC-RAS\\6.3.1\\Ras.exe "{self.ras_project._ras_text_file_path}" \
                "{self.ras_project._ras_root_path}{self.plan.file_extension}" -c'
            p = subprocess.Popen(runRAS)
            return p.pid


class RasTextFile:
    """Represents a HEC-RAS text file."""

    def __init__(self, ras_text_file_path, new_file=False):
        self._ras_text_file_path = ras_text_file_path

        if not new_file and not os.path.exists(ras_text_file_path):
            raise FileNotFoundError(f"could not find {ras_text_file_path}")
        else:
            self._ras_text_file_path = ras_text_file_path
            self._ras_root_path = os.path.splitext(self._ras_text_file_path)[0]

        if not new_file:
            self.read_contents()
        else:
            self.contents = []

    def __repr__(self):
        """Representation of the RasTextFile class."""
        return f"RasTextFile({self._ras_text_file_path})"

    def read_contents(self):
        """Read the contents of the text file."""
        if not os.path.exists(self._ras_text_file_path):
            raise FileNotFoundError(f"could not find {self._ras_text_file_path}")
        with open(self._ras_text_file_path) as f:
            self.contents = f.read().splitlines()

    def write_contents(self):
        """Write the contents of the text file."""
        if os.path.exists(self._ras_text_file_path):
            raise FileExistsError(f"The specified file already exists {self._ras_text_file_path}")

        logging.info(f"writing: {os.path.basename(self._ras_text_file_path)}")
        with open(self._ras_text_file_path, "w") as f:
            f.write("\n".join(self.contents))

    def write_updated_contents(self):
        """Write the updated contents of the text file."""
        if not os.path.exists(self._ras_text_file_path):
            raise FileNotFoundError(f"The specified file doesn't exists {self._ras_text_file_path}")

        logging.info(f"updating: {os.path.basename(self._ras_text_file_path)}")
        with open(self._ras_text_file_path, "w") as f:
            f.write("\n".join(self.contents))

    @property
    def file_extension(self):
        """Get the file extension."""
        return Path(self._ras_text_file_path).suffix


class RasProject(RasTextFile):
    """Represents a HEC-RAS project file."""

    def __init__(self, ras_text_file_path: str, new_file: bool = False):
        super().__init__(ras_text_file_path, new_file)

        if self.file_extension != ".prj":
            raise TypeError(f"Project extension must be .prj, not {self.file_extension}")

        self._ras_project_basename = os.path.splitext(os.path.basename(self._ras_text_file_path))[0]
        self._ras_dir = os.path.dirname(self._ras_text_file_path)
        os.makedirs(self._ras_dir, exist_ok=True)

        if new_file:
            self.contents = [
                f"Proj Title={self._ras_project_basename}",
                "Current Plan=",
            ]

    def __repr__(self):
        """Representation of the RasProject class."""
        return f"RasProject({self._ras_text_file_path})"

    @classmethod
    def from_str(cls, text_string: str, ras_text_file_path: str = ""):
        """Initiate a RasProject class from a string."""
        inst = cls(ras_text_file_path, new_file=True)
        inst.contents = text_string.splitlines()
        return inst

    @property
    def title(self):
        """Title of the HEC-RAS project."""
        return search_contents(self.contents, "Proj Title")

    @property
    def units(self):
        """Units of the HEC-RAS project."""
        if "English Units" in self.contents:
            return "English"
        else:
            return "Metric"

    @property
    @combine_root_extension
    def plans(self):
        """Get the plans associated with this project."""
        return [f".{ext}" for ext in search_contents(self.contents, "Plan File", expect_one=False)]

    @property
    @combine_root_extension
    def geoms(self):
        """Get the geometry files associated with this project."""
        return search_contents(self.contents, "Geom File", expect_one=False)

    @property
    @combine_root_extension
    def unsteady_flows(self):
        """Get the unsteady flow files associated with this project."""
        return search_contents(self.contents, "Unsteady File", expect_one=False)

    @property
    @combine_root_extension
    def steady_flows(self):
        """Get the steady flow files associated with this project."""
        return search_contents(self.contents, "Flow File", expect_one=False)

    @property
    def n_geoms(self):
        """Get the number of geometry files associated with this project."""
        return len(self.geoms)

    @property
    def n_plans(self):
        """Get the number of plans associated with this project."""
        return len(self.plans)

    @property
    def n_flows(self):
        """Get the number of flow files associated with this project."""
        return len(self.steady_flows)

    @property
    def current_plan(self):
        """Get the current plan."""
        return f".{search_contents(self.contents, 'Current Plan')}"

    def set_current_plan(self, plan_ext):
        """
        Return new contents with the specified plan as the current RAS plan.

        Args:
            plan_ext: The plan extension to set as the current plan
        """
        new_contents = self.contents
        if f"{plan_ext}" not in VALID_PLANS:
            raise TypeError(f"Plan extension must be one of .p01-.p99, not {plan_ext}")
        else:
            new_contents = replace_line_in_contents(new_contents, "Current Plan", plan_ext.lstrip("."))

        # TODO: Update this to put it with the other plans
        if f"Plan File={plan_ext.lstrip('.')}" not in new_contents:
            new_contents.append(f"Plan File={plan_ext.lstrip('.')}")
        logging.info("set plan!")
        return new_contents


class RasPlanText(RasTextFile):
    """Represents a HEC-RAS plan file."""

    def __init__(self, ras_text_file_path: str, crs: str = None, new_file: bool = False, units: str = "English"):
        super().__init__(ras_text_file_path, new_file)
        if self.file_extension not in VALID_PLANS:
            raise TypeError(f"Plan extension must be one of .p01-.p99, not {self.file_extension}")
        self.crs = crs
        self.hdf_file = self._ras_text_file_path + ".hdf"
        self.units = units

    def __repr__(self):
        """Representation of the RasPlanText class."""
        return f"RasPlanText({self._ras_text_file_path})"

    @classmethod
    def from_str(cls, text_string: str, crs, ras_text_file_path: str = ""):
        """Initiate a RasPlanText class from a string."""
        inst = cls(ras_text_file_path, crs, new_file=True)
        inst.contents = text_string.splitlines()
        return inst

    @property
    def title(self):
        """Title of this HEC-RAS plan."""
        return search_contents(self.contents, "Plan Title")

    @property
    def version(self):
        """HEC-RAS version."""
        return search_contents(self.contents, "Program Version")

    @property
    @combine_root_extension
    def plan_geom_file(self):
        """Geometry flow file associated with this plan."""
        return self.plan_geom_extension

    @property
    @combine_root_extension
    def plan_unsteady_flow_file(self):
        """Unsteady flow file associated with this plan."""
        return self.plan_unsteady_extension

    @property
    @combine_root_extension
    def plan_steady_file(self):
        """Steady flow file associated with this plan."""
        return self.plan_steady_extension

    @property
    def plan_geom_extension(self):
        """Geometry extension associated with this plan."""
        try:
            return f".{search_contents(self.contents, 'Geom File')}"
        except ValueError:
            raise NoGeometryFileSpecifiedError(
                f"Could not find a specified geometry file for plan: {self.title} | {self._ras_text_file_path}"
            )

    @property
    def plan_unsteady_extension(self):
        """Unsteady flow extension associated with this plan."""
        """Steady flow extension associated with this plan."""
        try:
            extension = f".{search_contents(self.contents, 'Flow File')}"
        except ValueError:
            raise NoFlowFileSpecifiedError(
                f"Could not find a specified flow file for plan: {self.title} | {self._ras_text_file_path}"
            )

        if "u" in extension:
            return f".{search_contents(self.contents, 'Flow File')}"
        else:
            raise ValueError(f"Expected an unsteady flow extension (.uxx). Received: {extension}")

    @property
    def plan_steady_extension(self):
        """Steady flow extension associated with this plan."""
        try:
            extension = f".{search_contents(self.contents, 'Flow File')}"
        except ValueError:
            raise NoFlowFileSpecifiedError(
                f"Could not find a specified flow file for plan: {self.title} | {self._ras_text_file_path}"
            )
        if "f" in extension:
            return f".{search_contents(self.contents, 'Flow File')}"
        else:
            raise ValueError(f"Expected an steady flow extension (.fxx). Recieved: {extension}")

    @property
    @check_crs
    def geom(self):
        """Represents the HEC-RAS geometry file associated with this plan."""
        return RasGeomText(self.plan_geom_file, self.crs, units=self.units)

    @property
    def flow(self):
        """Represents the HEC-RAS flow file associated with this plan."""
        return RasFlowText(self.plan_steady_file)

    def new_plan_from_existing(self, title, short_id, geom_ext, flow_ext):
        """Populate the content of the new plan with basic attributes (title, short_id, flow, and geom)."""
        new_contents = self.contents
        if len(title) > 80:
            raise ValueError("Short Identifier must be less than 80 characters")
        else:
            new_contents = replace_line_in_contents(new_contents, "Plan Title", title)

        if len(short_id) > 80:
            raise ValueError("Short Identifier must be less than 80 characters")
        else:
            new_contents = replace_line_in_contents(new_contents, "Short Identifier", short_id)

        if f".{geom_ext}" not in VALID_GEOMS:
            raise TypeError(f"Geometry extension must be one of g01-g99, not {geom_ext}")
        else:
            new_contents = replace_line_in_contents(new_contents, "Geom File", geom_ext)

        if f".{flow_ext}" not in VALID_STEADY_FLOWS:
            raise TypeError(f"Flow extension must be one of f01-f99, not {flow_ext}")
        else:
            new_contents = replace_line_in_contents(new_contents, "Flow File", flow_ext)

        try:
            new_contents = replace_line_in_contents(new_contents, "Run RASMapper", "-1")
        except ValueError:
            new_contents.append("Run RASMapper=-1 ")

        return new_contents

    def new_plan_contents(self, title: str, short_id: str, flow, geom, run_rasmapper: bool = False):
        """
        Populate the content of the plan with basic attributes (title, short_id, flow, and geom).

        Raises
        ------
            RuntimeError: raise run time error if the plan already has content associated with it
        """
        if self.contents:
            raise RuntimeError(f"content already exists for this plan: {self._ras_text_file_path}")

        # create necessary lines for the content of the plan text file.
        if len(title) > 80:
            raise ValueError("Short Identifier must be less than 80 characters")
        else:
            self.contents.append(f"Plan Title={title}")
        if len(short_id) > 80:
            raise ValueError("Short Identifier must be less than 80 characters")
        else:
            self.contents.append(f"Short Identifier={short_id}")

        if f"{geom.file_extension}" not in VALID_GEOMS:
            raise TypeError(f"Geometry extension must be one of .g01-.g99, not {geom.file_extension}")
        else:
            self.contents.append(f"Geom File={geom.file_extension.lstrip('.')}")

        if f"{flow.file_extension}" not in VALID_STEADY_FLOWS:
            raise TypeError(f"Flow extension must be one of .f01-.f99, not {flow.file_extension}")
        else:
            self.contents.append(f"Flow File={flow.file_extension.lstrip('.')}")
        if run_rasmapper:
            self.contents.append("Run RASMapper=-1 ")
        else:
            self.contents.append("Run RASMapper= 0 ")

    def read_plan_results(self, profile_name_map: dict) -> dict:
        """
        Read the flow and water surface elevations resulting from the computed plan.

        Raises
        ------
            FileNotFoundError: _description_

        Returns
        -------
            dict: A dictionary containing "wse" and "flow" keys whose values are pandas dataframes
        """
        # check if the hdf file exists; raise error if it does not
        if not self.hdf_file:
            self.hdf_file = self.text_file + ".hdf"

            if not os.path.exists(self.hdf_file):
                raise FileNotFoundError(f'The file "{self.hdf_file}" does not exists')

        def remove_multiple_spaces(x):
            return re.sub(" +", " ", x)

        # read the hdf file
        with h5py.File(self.hdf_file) as hdf:
            # get columns and indexes for the wse and flow arrays
            columns = decode(pd.DataFrame(hdf[XS_NAMES_HDF_PATH]))
            index = decode(pd.DataFrame(hdf[PROFILE_NAMES_HDF_PATH]))

            # remove multiple spaces in between the river-reach-riverstation ids.
            columns[0] = columns[0].apply(remove_multiple_spaces)

            # create dataframes for the wse and flow results
            wse = pd.DataFrame(
                hdf.get(WSE_HDF_PATH), columns=columns[0].values, index=[profile_name_map[i] for i in index[0].values]
            ).T
            flow = pd.DataFrame(
                hdf.get(FLOW_HDF_PATH), columns=columns[0].values, index=[profile_name_map[i] for i in index[0].values]
            ).T

        return wse, flow


class RasGeomText(RasTextFile):
    """Represents a HEC-RAS geometry text file."""

    def __init__(self, ras_text_file_path: str, crs: str = None, new_file=False, units: str = "English"):
        super().__init__(ras_text_file_path, new_file)
        if not new_file and self.file_extension not in VALID_GEOMS:
            raise TypeError(f"Geometry extension must be one of .g01-.g99, not {self.file_extension}")

        self.crs = CRS(crs)
        self.units = units
        self.hdf_file = self._ras_text_file_path + ".hdf"

        if len(ras_text_file_path) > 0:
            self.fix_htab_errors()

    def __repr__(self):
        """Representation of the RasGeomText class."""
        return f"RasGeomText({self._ras_text_file_path})"

    @classmethod
    def from_str(cls, text_string: str, crs, ras_text_file_path: str = ""):
        """Initiate the RASGeomText class from a string."""
        inst = cls(ras_text_file_path, crs, new_file=True)
        inst.contents = text_string.splitlines()
        return inst

    @classmethod
    def from_gpkg(cls, gpkg_path, title: str, version: str, ras_text_file_path: str = ""):
        """Initiate the RASGeomText class from a geopackage."""
        inst = cls(ras_text_file_path, crs=gpd.read_file(gpkg_path, layer="XS").crs, new_file=True)
        inst._gpkg_path = gpkg_path
        inst._version = version
        inst._title = title

        inst.contents = inst._content_from_gpkg
        inst.fix_htab_errors()
        return inst

    @property
    def _content_from_gpkg(
        self,
    ):
        if not hasattr(self, "_gpkg_path"):
            raise ("gpkg_path not provided")
        if not os.path.exists(self._gpkg_path):
            raise FileNotFoundError(f"Could not find the specified gpkg_path {self._gpkg_path}")

        self._check_layers()
        xs_gdf = gpd.read_file(self._gpkg_path, layer="XS", driver="GPKG")
        # headers
        gpkg_data = (
            f"Geom Title={self._title}\nProgram Version={self._version}\nViewing Rectangle={self._bbox(xs_gdf)}\n\n"
        )

        # junction data
        if "Junction" in fiona.listlayers(self._gpkg_path):
            junction_gdf = gpd.read_file(self._gpkg_path, layer="Junction", driver="GPKG")
            junction_gdf["ras_data"].str.cat(sep="\n")

        # river reach data
        gpkg_data += self._river_reach_data_from_gpkg

        return gpkg_data.splitlines()

    @property
    def _river_reach_data_from_gpkg(self):
        river_gdf = gpd.read_file(self._gpkg_path, layer="River", driver="GPKG")
        xs_gdf = gpd.read_file(self._gpkg_path, layer="XS", driver="GPKG")

        if "Structure" in fiona.listlayers(self._gpkg_path):
            structure_gdf = gpd.read_file(self._gpkg_path, layer="Structure", driver="GPKG")
            node_gdf = pd.concat([xs_gdf, structure_gdf]).sort_values(by="river_station", ascending=False)
        else:
            node_gdf = xs_gdf.sort_values(by="river_station", ascending=False)

        data = ""
        for _, row in river_gdf.iterrows():
            centroid = row.geometry.centroid

            coords = row["geometry"].coords

            data += f"River Reach={row['river'].ljust(16)},{row['reach'].ljust(16)}\n"
            data += f"Reach XY= {len(coords)} \n"

            for i, (x, y) in enumerate(coords):
                data += str(x)[:16].rjust(16) + str(y)[:16].rjust(16)
                if i % 2 != 0:
                    data += "\n"

            data += f"\nRch Text X Y={centroid.x},{centroid.y}\nReverse River Text= 0 \n\n"

            # cross section and structures data
            data += node_gdf.loc[node_gdf["river_reach"] == row["river_reach"], "ras_data"].str.cat(sep="\n")

        return data

    def _bbox(self, gdf):
        bounds = gdf.total_bounds
        return f"{bounds[0]} , {bounds[2]} , {bounds[3]} , {bounds[1]} "

    def _check_layers(self):
        layers = set(fiona.listlayers(self._gpkg_path)) & set(SUPPORTED_LAYERS)

        if "XS" not in layers:
            raise NoCrossSectionLayerError(f"Could not find a layer called XS in {self._gpkg_path}")

        if "River" not in layers:
            raise NoRiverLayerError(f"Could not find a layer called River in {self._gpkg_path}")

    def fix_htab_errors(self):
        """Update any htab values lower than the section invert to the section invert."""
        working_string = "\n".join(self.contents.copy())
        needs_save = False
        for xs in self.cross_sections.values():
            if xs.has_htab_error:
                needs_save = True
                logging.info(f"Fixing htab error for {xs.river_reach}")
                old_htab_str = xs.htab_string
                # HEC-RAS default handling:
                # either 0 or 0.5 ft above section invert for the start elevation
                # increment that will yield 20 pts between start and section max elevations
                # We want to preserve engineer-specified increments, so we don't do that
                new_htab_str = old_htab_str.replace(str(xs.htab_starting_el), str(xs.thalweg))

                old_xs_str = "\n".join(xs.ras_data)
                new_xs_str = old_xs_str.replace(old_htab_str, new_htab_str)
                working_string = working_string.replace(old_xs_str, new_xs_str)
        if needs_save:
            with open(self._ras_text_file_path, "w") as f:
                f.write(working_string)
            self.contents = working_string.splitlines()

    @property
    def title(self):
        """Title of the HEC-RAS Geometry file."""
        return search_contents(self.contents, "Geom Title")

    @property
    def version(self):
        """The HEC-RAS version."""
        return search_contents(self.contents, "Program Version", expect_one=False)

    @property
    @check_crs
    def reaches(self) -> dict:
        """A dictionary of the reaches contained in the HEC-RAS geometry file."""
        river_reaches = search_contents(self.contents, "River Reach", expect_one=False)
        reaches = {}
        for river_reach in river_reaches:
            reaches[river_reach] = Reach(self.contents, river_reach, self.crs, self.units)
        return reaches

    @property
    @check_crs
    def rivers(self) -> dict:
        """A nested river-reach dictionary of the rivers/reaches contained in the HEC-RAS geometry file."""
        rivers = {}
        for reach in self.reaches.values():
            rivers[reach.river] = {}
            rivers[reach.river].update({reach.reach: reach})
        return rivers

    @property
    @check_crs
    def junctions(self) -> dict:
        """A dictionary of the junctions contained in the HEC-RAS geometry file."""
        juncts = search_contents(self.contents, "Junct Name", expect_one=False)
        junctions = {}
        for junct in juncts:
            junctions[junct] = Junction(self.contents, junct, self.crs)
        return junctions

    @property
    @lru_cache
    @check_crs
    def cross_sections(self) -> dict:
        """A dictionary of the cross sections contained in the HEC-RAS geometry file."""
        cross_sections = {}
        for reach in self.reaches.values():
            cross_sections.update(reach.cross_sections)

        return cross_sections

    @property
    @check_crs
    def structures(self) -> dict:
        """A dictionary of the structures contained in the HEC-RAS geometry file."""
        structures = {}
        for reach in self.reaches.values():
            structures.update(reach.structures)

        return structures

    def determine_lateral_structure_xs(self, xs_gdf):
        """
        Determine if the cross sections are connected to lateral structure.

        Determine if the cross sections are connected to lateral structures,
        if they are update 'has_lateral_structures' to True.
        """
        for structure in self.structures.values():
            if int(structure.type) == 6:
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

                except InvalidStructureDataError as e:
                    pass

        return xs_gdf

    @property
    @lru_cache
    @check_crs
    def reach_gdf(self):
        """A GeodataFrame of the reaches contained in the HEC-RAS geometry file."""
        return pd.concat([reach.gdf for reach in self.reaches.values()], ignore_index=True)

    @property
    @lru_cache
    @check_crs
    def junction_gdf(self):
        """A GeodataFrame of the junctions contained in the HEC-RAS geometry file."""
        if self.junctions:
            return pd.concat(
                [junction.gdf for junction in self.junctions.values()],
                ignore_index=True,
            )

    @property
    @lru_cache
    @check_crs
    def xs_gdf(self):
        """Geodataframe of all cross sections in the geometry text file."""
        gdf = pd.concat([xs.gdf for xs in self.cross_sections.values()], ignore_index=True)
        return self.determine_lateral_structure_xs(gdf)

    @property
    @lru_cache
    @check_crs
    def structures_gdf(self):
        """Geodataframe of all structures in the geometry text file."""
        return pd.concat([structure.gdf for structure in self.structures.values()], ignore_index=True)

    @property
    @check_crs
    def n_cross_sections(self):
        """Number of cross sections in the HEC-RAS geometry file."""
        return len(self.cross_sections)

    @property
    @check_crs
    def n_structures(self):
        """Number of structures in the HEC-RAS geometry file."""
        return len(self.structures)

    @property
    @check_crs
    def n_reaches(self):
        """Number of reaches in the HEC-RAS geometry file."""
        return len(self.reaches)

    @property
    @check_crs
    def n_junctions(self):
        """Number of junctions in the HEC-RAS geometry file."""
        return len(self.junctions)

    @property
    @check_crs
    def n_rivers(self):
        """Number of rivers in the HEC-RAS geometry file."""
        return len(self.rivers)

    @check_crs
    def to_gpkg(self, gpkg_path: str):
        """Write the HEC-RAS Geometry file to geopackage."""
        self.xs_gdf.to_file(gpkg_path, driver="GPKG", layer="XS")
        self.reach_gdf.to_file(gpkg_path, driver="GPKG", layer="River")
        if self.junctions:
            self.junction_gdf.to_file(gpkg_path, driver="GPKG", layer="Junction")
        if self.structures:
            self.structures_gdf.to_file(gpkg_path, driver="GPKG", layer="Structure")


class RasFlowText(RasTextFile):
    """Represents a HEC-RAS flow text file."""

    def __init__(self, ras_text_file_path: str, new_file: bool = False):
        super().__init__(ras_text_file_path, new_file)
        if self.file_extension in VALID_UNSTEADY_FLOWS or self.file_extension in VALID_QUASISTEADY_FLOWS:
            raise NotImplementedError("only steady flow (.f**) supported")

        if self.file_extension not in VALID_STEADY_FLOWS:
            raise TypeError(f"Flow extension must be one of .f01-.f99, not {self.file_extension}")

    def __repr__(self):
        """Representation of the RasFlowText class."""
        return f"RasFlowText({self._ras_text_file_path})"

    @classmethod
    def from_str(cls, text_string: str, ras_text_file_path: str = ""):
        """Read flow file from string."""
        inst = cls(ras_text_file_path, new_file=True)
        inst.contents = text_string.splitlines()
        return inst

    @property
    def title(self):
        """Title of the flow File."""
        return search_contents(self.contents, "Flow Title")

    @property
    def version(self):
        """Program Version."""
        return search_contents(self.contents, "Program Version")

    @property
    def n_profiles(self):
        """Number of profiles."""
        return len(self.profile_names)

    @property
    def profile_names(self):
        """Profile names."""
        return search_contents(self.contents, "Profile Names").split(",")

    def write_headers(self, title: str, profile_names: list[str], description: str):
        """
        Write headers for flow content.

        Args:
            title (str): title of the flow
            profile_names (list[str]): profile names for the flow

        Returns
        -------
            list (list[str]): lines of the flow content
        """
        lines = [
            f"Flow Title={title}",
            f"Number of Profiles= {len(profile_names)}",
            "BEGIN FILE DESCRIPTION:",
            description,
            "END FILE DESCRIPTION:",
            f"Profile Names={','.join([str(pn) for pn in profile_names])}",
        ]
        return lines

    def write_discharges(self, flows: list, river: str, reach: str, river_station: float):
        """
        Write discharges to flow content.

        Args:
            flows (list): flows to write to the flow content
            river (str): Ras river
            reach (str): Ras reach
            river_station (float): Ras river station
        """
        lines = []
        lines.append(f"River Rch & RM={river},{reach.ljust(16,' ')},{str(river_station).ljust(8,' ')}")
        line = ""
        for i, flow in enumerate(flows):
            line += f"{str(flow).rjust(8,' ')}"
            if (i + 1) % 10 == 0:
                lines.append(line)
                line = ""

        if (i + 1) % 10 != 0:
            lines.append(line)
        return lines

    def write_ds_known_wse(self, ds_wses: list[float], river: str, reach: str):
        """
        Write downstream known water surface elevations to flow content.

        Args:
            ds_wses (list): downstream known water surface elevations
            river (str): Ras river
            reach (str): Ras reach
        """
        lines = []
        for count, wse in enumerate(ds_wses):
            lines.append(f"Boundary for River Rch & Prof#={river},{reach.ljust(16,' ')}, {count+1}")
            lines.append("Up Type= 0 ")
            lines.append("Dn Type= 1 ")
            lines.append(f"Dn Known WS={wse}")

        return lines

    def write_ds_normal_depth(self, number_of_profiles: int, normal_depth: float, river: str, reach: str):
        """
        Write the downstream normal depth boundary condition.

        Args:
            number_of_profiles (int): Number of profiles
            normal_depth (float): Normal depth slope to apply to all profiles
            river (str): Ras river
            reach (str): Ras reach
        """
        lines = []
        for i in range(number_of_profiles):
            lines.append(f"Boundary for River Rch & Prof#={river},{reach.ljust(16,' ')}, {i+1}")
            lines.append("Up Type= 0 ")
            lines.append("Dn Type= 3 ")
            lines.append(f"Dn Slope={normal_depth}")

        return lines

    @property
    def n_flow_change_locations(self):
        """Number of flow change locations."""
        return len(search_contents(self.contents, "River Rch & RM", expect_one=False))

    @property
    def flow_change_locations(self):
        """Retrieve flow change locations."""
        flow_change_locations, locations = [], []
        for location in search_contents(self.contents, "River Rch & RM", expect_one=False):
            # parse river, reach, and river station for the flow change location
            river, reach, rs = location.split(",")
            lines = text_block_from_start_end_str(
                f"River Rch & RM={location}", ["River Rch & RM", "Boundary for River Rch & Prof#"], self.contents
            )
            flows = []

            for line in lines[1:]:

                if "River Rch & RM" in line:
                    break
                for i in range(0, len(line), 8):
                    flows.append(float(line[i : i + 8].lstrip(" ")))
                    if len(flows) == self.n_profiles:
                        flow_change_locations.append(
                            FlowChangeLocation(
                                river,
                                reach.rstrip(" "),
                                float(rs.replace("*", "")),
                                flows,
                                self.profile_names,
                            )
                        )

                    if len(flow_change_locations) == self.n_flow_change_locations:
                        return flow_change_locations

    @property
    def description(self):
        """Fetch the description of the flow file."""
        return text_block_from_start_end_str("BEGIN FILE DESCRIPTION:", "END FILE DESCRIPTION:", self.contents, 1)[1]


class RasUnsteadyText(RasTextFile):
    """Represents a HEC-RAS unsteady flow text file."""

    def __init__(self, ras_text_file_path: str, new_file: bool = False):
        super().__init__(ras_text_file_path, new_file)
        if self.file_extension in VALID_STEADY_FLOWS or self.file_extension in VALID_QUASISTEADY_FLOWS:
            raise NotImplementedError("only steady flow (.u**) supported")

        if self.file_extension not in VALID_UNSTEADY_FLOWS:
            raise TypeError(f"Flow extension must be one of .u01-.u99, not {self.file_extension}")

    def __repr__(self):
        """Representation of the RasUnsteadyText class."""
        return f"RasFlowText({self._ras_text_file_path})"

    @classmethod
    def from_str(cls, text_string: str, ras_text_file_path: str = ""):
        """Read flow file from string."""
        inst = cls(ras_text_file_path, new_file=True)
        inst.contents = text_string.splitlines()
        return inst

    @property
    def title(self):
        """Title of the flow File."""
        return search_contents(self.contents, "Flow Title")

    @property
    def version(self):
        """Program Version."""
        return search_contents(self.contents, "Program Version")


class RasMap:
    """
    Represents a single RasMapper file.

    Attributes
    ----------
    text_file : Text file representing the RAS Mapper file
    contents : a text representation of the files contents
    version : HEC-RAS version for this RAS Mapper file

    """

    def __init__(self, path: str, geom, version: str = 631):
        """
        Represent a single RasMapper file.

        Args:
            path (str): file path to the plan text file
            version (str): HEC-RAS vesion for this RAS Mapper file
        """
        self.text_file = path
        self.version = version

        if os.path.exists(path):
            self.read_contents()
        else:
            self.new_rasmap_content(geom)

    def __repr__(self):
        """Representation of the RasMap class."""
        return f"RasMap({self.path})"

    def read_contents(self):
        """
        Read contents of the file. Searches for the file locally and on s3.

        Raises
        ------
            FileNotFoundError:
        """
        if os.path.exists(self.text_file):
            with open(self.text_file) as f:
                self.contents = f.read()
        else:
            raise FileNotFoundError(f"could not find {self.text_file}")

    def new_rasmap_content(self, geom):
        """Populate the contents with boiler plate contents."""
        if self.version in ["631", "6.31", "6.3.1", "63.1"]:
            self.contents = RASMAP_631.replace("geom_hdf_placeholder", os.path.basename(geom.hdf_file)).replace(
                "geom_name_placeholder", geom.title
            )
        else:
            raise ValueError(f"model version '{self.version}' is not supported")

    def update_crs(self, projection_file: str):
        """
        Add/update the crs file to the RAS Mapper contents.

        Args:
            projection_file (str): path to projeciton file containing the coordinate system (.prj)

        Raises
        ------
            FileNotFoundError:
        """
        directory = os.path.dirname(self.text_file)
        crs_base = os.path.basename(projection_file)

        if crs_base not in os.listdir(directory):
            raise FileNotFoundError(
                f"Expected crs file to be in RAS directory: {directory}. Provided location is: {projection_file}"
            )

        lines = self.contents.splitlines()
        lines.insert(2, rf'  <RASProjectionFilename Filename=".\{crs_base}" />')

        self.contents = "\n".join(lines)

    def add_result_layers(self, plan_short_id: str, profiles: list[str], variable: str):
        """
        Add results layers to RasMap contents. When the RAS plan is ran with "Floodplain Mapping" toggled onthe result layer added here will output rasters.

        Args:
            plan_short_id (str): Plan short id for the output raster(s)
            profiles (list[str]): Profiles for the output raster(s)
            variable (str): Variable to create rasters for. Currently "Depth" is the only supported variable.
        """
        if variable not in ["Depth"]:
            raise NotImplementedError(
                f"Variable {variable} not currently implemented. Currently only Depth is supported."
            )

        lines = []
        for line in self.contents.splitlines():
            if line == "  </Results>":
                for i, profile in enumerate(profiles):
                    lines.append(
                        rf'      <Layer Name="{variable}" Type="RASResultsMap" Checked="True" Filename=".\{plan_short_id}\{variable} ({profile}).vrt">'
                    )
                    lines.append(
                        rf'        <MapParameters MapType="{variable.lower()}" OutputMode="Stored Current Terrain" StoredFilename=".\{plan_short_id}\{variable} ({profile}).vrt" ProfileIndex="{i}" ProfileName="{profile}" />'
                    )
                    lines.append("      </Layer>")
                lines.append("    </Layer>")
            lines.append(line)

        self.contents = "\n".join(lines)

    def add_plan_layer(self, plan_short_id: str, plan_hdf: str, profiles: list[str]):
        """
        Add a plan layer to the results in the RASMap contents.

        Args:
            plan_short_id (str): plan_short_id of the plan
            plan_hdf (str): hdf file for the plan
            profiles (list[str]): profiles for the plan
        """
        lines = []
        for line in self.contents.splitlines():
            if line == "  <Results />":
                lines.append(
                    PLAN.replace("plan_hdf_placeholder", plan_hdf)
                    .replace("plan_name_placeholder", str(plan_short_id))
                    .replace("profile_placeholder", profiles[0])
                )
                continue

            lines.append(line)

        self.contents = "\n".join(lines)

    def add_terrain(self, terrain_name: str, terrain_path: str):
        """Add Terrain to RasMap content."""
        lines = []
        for line in self.contents.splitlines():
            if line == "  <Terrains />":
                lines.append(TERRAIN.replace(TERRAIN_NAME, terrain_name).replace(TERRAIN_PATH, terrain_path))
                continue

            lines.append(line)

        self.contents = "\n".join(lines)

    def write(self):
        """Write Ras Map contents to file."""
        logging.info(f"writing: {os.path.basename(self.text_file)}")

        with open(self.text_file, "w") as f:
            f.write(self.contents)

        # write backup
        with open(self.text_file + ".backup", "w") as f:
            f.write(self.contents)


# functions
def search_for_ras_crs(search_dir: str):
    """Search for RAS crs."""
    rasmap_files = glob.glob(f"{search_dir}/*.rasmap")
    if rasmap_files:
        rm = rasmap_files[0]
    else:
        raise FileNotFoundError(f"Could not find rasmap file in {search_dir}")

    with open(rm) as f:
        for line in f.readlines():
            if "RASProjectionFilename Filename=" in line:
                relative_path = line.split("=")[-1].split('"')[1].lstrip(".")
                abs_path = f"{search_dir}/{relative_path}"
                if os.path.exists(abs_path):
                    with open(abs_path) as src:
                        crs = src.read()
                        return crs, abs_path
                else:
                    raise FileNotFoundError(f"Could not find crs file in {search_dir}")


def get_new_extension_number(dict_of_ras_subclasses: dict) -> str:
    """
    Determine the next numeric extension that should be used when creating a new plan, flow, or geom; e.g., if you are adding a new plan and .p01, and .p02 already exists then the new planwill have a .p03 extension.

    Args:
        dict_of_ras_subclasses (dict): A dictionary containing plan/geom/flow titles as keys
            and objects plan/geom/flow as values.

    Returns
    -------
        new file extension (str): The new file exension.
    """
    extension_number = []
    if not dict_of_ras_subclasses:
        return "01"
    for val in dict_of_ras_subclasses.values():
        extension_number.append(int(val.file_extension[2:]))

    return f"{(max(extension_number)+1):02d}"


def create_terrain(
    src_terrain_filepaths: list[str],
    projection_file: str,
    dst_terrain_filepath: str,
    vertical_units: str = "Feet",
    version: str = "631",
) -> str:
    r"""
    Use the crs file and a list of terrain file paths to make the RAS terrain HDF file. Default location is {model_directory}\Terrain\Terrain.hdf.

    Returns the full path to the local directory containing the output files.
    """
    if vertical_units not in ["Feet", "Meters"]:
        raise ValueError(f"vertical_units must be either 'Feet' or 'Meters'; got: '{vertical_units}'")

    missing_files = [x for x in src_terrain_filepaths if not os.path.exists(x)]

    if missing_files:
        raise FileNotFoundError(str(missing_files))

    terrain_exe = get_terrain_exe_path(version)
    if not os.path.isfile(terrain_exe):
        raise FileNotFoundError(terrain_exe)

    exe_parent_dir = os.path.split(terrain_exe)[0]
    # TODO: Add documentation for the following to understand
    # what files are created and where they are stored
    subproc_args = [
        terrain_exe,
        "CreateTerrain",
        f"units={vertical_units}",  # vertical units
        "stitch=true",
        f"prj={projection_file}",
        f"out={dst_terrain_filepath}",
    ]
    # add list of input rasters from which to build the Terrain
    subproc_args.extend([os.path.abspath(p) for p in src_terrain_filepaths])
    logging.debug(
        f"Running the following args, from {os.path.basename(exe_parent_dir)}:" + "\n  ".join([""] + subproc_args)
    )
    subprocess.check_call(subproc_args, cwd=exe_parent_dir, stdout=subprocess.DEVNULL)
    return {"RAS Terrain": dst_terrain_filepath}

    # TODO this recompression does work but RAS does not accept the recompressed tif for unknown reason...
    # # compress the output tif(s) that RasProcess.exe created (otherwise could be 1+ GB at HUC12 size)
    # layer_name = os.path.splitext(hdf_filename)[0]  # hdf file without extension
    # tif_pattern = rf"^{layer_name}\..+\.tif$"
    # output_tifs = [
    #     os.path.join(terrain_dir_fp, fn) for fn in os.listdir(terrain_dir_fp) if re.fullmatch(tif_pattern, fn)
    # ]
    # for tif in output_tifs:
    #     utils.recompress_tif(tif)
    #     utils.build_tif_overviews(tif)
