"""Subset a geopackage based on clonfation with NWM hydrofabric."""

import json
import logging
import os
import sqlite3
import warnings
from functools import lru_cache
from pathlib import Path

import fiona
import geopandas as gpd
import pandas as pd
from shapely import LineString
from shapely.ops import split

import ripple1d
from ripple1d.data_model import NwmReachModel, RippleSourceDirectory
from ripple1d.errors import SingleXSModel
from ripple1d.utils.ripple_utils import (
    RASWalker,
    clip_ras_centerline,
    fix_reversed_xs,
    xs_concave_hull,
)
from ripple1d.utils.sqlite_utils import create_non_spatial_table

warnings.filterwarnings("ignore")


class RippleGeopackageSubsetter:
    """Subset a geopackage based on conflation with NWM hydrofabric."""

    def __init__(
        self,
        src_gpkg_path: str,
        conflation_json: str,
        dst_project_dir: str,
        nwm_id: str = None,
        min_flow_multiplier_ras: float = 1,
        max_flow_multiplier_ras: float = 1,
        ignore_ras_flows: bool = False,
        ignore_nwm_flows: bool = False,
    ):

        self.src_gpkg_path = src_gpkg_path
        self.conflation_json = conflation_json
        self.dst_project_dir = dst_project_dir
        self.nwm_id = nwm_id
        self.min_flow_multiplier_ras = min_flow_multiplier_ras
        self.max_flow_multiplier_ras = max_flow_multiplier_ras
        self.ignore_ras_flows = ignore_ras_flows
        self.ignore_nwm_flows = ignore_nwm_flows

        self.walker = RASWalker(self.src_gpkg_path)

    @property
    @lru_cache
    def source_model_metadata(self):
        """Metadata from the source model."""
        with sqlite3.connect(self.src_gpkg_path) as conn:
            cur = conn.cursor()
            res = cur.execute("SELECT key,value from metadata")
            return dict(res.fetchall())

    def copy_metadata_to_ripple1d_gpkg(self):
        """Copy metadata table from source geopackage to ripple1d geopackage."""
        flow_file_extension = Path(
            self.source_model_metadata["primary_flow_file"]
        ).suffix
        # TODO I think the following code can be hardened by using regex or at a minimum usind pythons built in 'startswith' method for strings.
        if "f" in flow_file_extension:
            create_non_spatial_table(
                self.ripple_gpkg_file,
                {"units": self.source_model_metadata["units"], "steady": "True"},
            )
        elif "u" in flow_file_extension or "q" in flow_file_extension:
            create_non_spatial_table(
                self.ripple_gpkg_file,
                {"units": self.source_model_metadata["units"], "steady": "False"},
            )
        else:
            if not self.ignore_ras_flows:
                raise ValueError(
                    f"Expected forcing extension to be .fxx, .uxx, or .qxx. Received {flow_file_extension} for submodel: {self.nwm_id}"
                )
            else:
                logging.warning(
                    f"Skipping flow file validation as ignore_ras_flows is True. Received extension: {flow_file_extension} for submodel: {self.nwm_id}"
                )

    @property
    @lru_cache
    def conflation_parameters(self) -> dict:
        """Extract conflation parameters from the conflation json."""
        with open(self.conflation_json, "r") as f:
            conflation_parameters = json.load(f)
        return conflation_parameters

    @property
    def ripple1d_parameters(self) -> dict:
        """Extract ripple1d parameters from the conflation json."""
        return self.conflation_parameters["reaches"][self.nwm_id]

    @property
    def us_reach(self) -> str:
        """Extract upstream reach from conflation parameters."""
        return self.ripple1d_parameters["us_xs"]["reach"]

    @property
    def us_river(self) -> str:
        """Extract upstream river from conflation parameters."""
        return self.ripple1d_parameters["us_xs"]["river"]

    @property
    def us_rs(self) -> str:
        """Extract upstream river station from conflation parameters."""
        return float(self.ripple1d_parameters["us_xs"]["xs_id"])

    @property
    def us_river_reach(self) -> str:
        """Extract upstream river_reach from conflation parameters."""
        row = self.source_river[
            (self.source_river["river"] == self.us_river)
            & (self.source_river["reach"] == self.us_reach)
        ]
        return row.iloc[0]["river_reach"]

    @property
    def us_river_reach_rs(self) -> str:
        """Extract upstream river_reach_rs from conflation parameters."""
        return f"{self.us_river} {self.us_reach} {self.us_rs}"

    @property
    def ds_river(self) -> str:
        """Extract downstream river from conflation parameters."""
        return self.ripple1d_parameters["ds_xs"]["river"]

    @property
    def ds_reach(self) -> str:
        """Extract downstream reach from conflation parameters."""
        return self.ripple1d_parameters["ds_xs"]["reach"]

    @property
    def ds_rs(self) -> str:
        """Extract downstream river station from conflation parameters."""
        return float(self.ripple1d_parameters["ds_xs"]["xs_id"])

    @property
    def ds_river_reach(self) -> str:
        """Extract downstream river_reach from conflation parameters."""
        row = self.source_river[
            (self.source_river["river"] == self.ds_river)
            & (self.source_river["reach"] == self.ds_reach)
        ]
        return row.iloc[0]["river_reach"]

    @property
    def ds_river_reach_rs(self) -> str:
        """Extract downstream river_reach_rs from conflation parameters."""
        return f"{self.ds_river} {self.ds_reach} {self.ds_rs}"

    @property
    @lru_cache
    def source_xs(self) -> gpd.GeoDataFrame:
        """Extract cross sections from the source geopackage."""
        xs = gpd.read_file(self.src_gpkg_path, layer="XS")
        xs_subsets = []
        for _, row in self.source_river.iterrows():
            xs_subset = xs.loc[xs["river_reach"] == row["river_reach"]]
            xs_subsets.append(xs_subset.loc[xs_subset.intersects(row.geometry)])
        return pd.concat(xs_subsets).reset_index(drop=True)

    @property
    @lru_cache
    def source_hulls(self) -> gpd.GeoDataFrame:
        """Extract cross sections from the source geopackage."""
        return gpd.read_file(self.src_gpkg_path, layer="XS_concave_hull")

    @property
    @lru_cache
    def source_river(self) -> gpd.GeoDataFrame:
        """Extract river geometry from the source geopackage."""
        return gpd.read_file(self.src_gpkg_path, layer="River")

    @property
    @lru_cache
    def source_structure(self) -> gpd.GeoDataFrame:
        """Extract structures from the source geopackage."""
        if "Structure" in fiona.listlayers(self.src_gpkg_path):
            structures = gpd.read_file(self.src_gpkg_path, layer="Structure")
            source_structure = structures[
                structures.intersects(self.source_river.union_all())
            ]
            return source_structure

    @property
    @lru_cache
    def source_junction(self) -> gpd.GeoDataFrame:
        """Extract junctions from the source geopackage."""
        if "Junction" in fiona.listlayers(self.src_gpkg_path):
            source_junction = gpd.read_file(self.src_gpkg_path, layer="Junction")
            return source_junction

    @property
    def ripple_us_xs(self):
        """The most upstream cross section for the ripple model."""
        return self.ripple_xs.loc[
            self.ripple_xs["river_station"] == self.ripple_xs["river_station"].max(),
            "geometry",
        ].iloc[0]

    @property
    def ripple_ds_xs(self):
        """The most downstream cross section for the ripple model."""
        return self.ripple_xs.loc[
            self.ripple_xs["river_station"] == self.ripple_xs["river_station"].min(),
            "geometry",
        ].iloc[0]

    @property
    def split_source_hull(self):
        """Split the concave hull of the source model using the upstream and downstream cross sections of the submodel."""
        geoms = split(self.source_hulls.geometry.iloc[0], self.ripple_us_xs).geoms
        hulls = []
        for geom in geoms:
            if geom.intersects(self.ripple_us_xs) and geom.intersects(
                self.ripple_ds_xs
            ):
                candidate_geoms = split(geom, self.ripple_ds_xs).geoms
                for candidate_geom in candidate_geoms:
                    if candidate_geom.intersects(
                        self.ripple_us_xs
                    ) and candidate_geom.intersects(self.ripple_ds_xs):
                        hulls.append(candidate_geom)
        if len(hulls) != 1:
            raise ValueError(
                f"Expected 1 polygon for ripple xs concave hull; got: {len(hulls)} | network id: {self.nwm_id}"
            )
        return hulls

    @property
    @lru_cache
    def ripple_xs_concave_hull(self):
        """Get the concave hull of the cross sections."""
        return xs_concave_hull(fix_reversed_xs(self.ripple_xs, self.ripple_river))

    @property
    @lru_cache
    def subset_reaches(self) -> list[str]:
        """Get a list of river_reach values on path from u/s to d/s XS."""
        return self.walker.walk(self.us_river_reach, self.ds_river_reach)

    @property
    @lru_cache
    def reach_distance_modifiers(self) -> dict:
        """Get a dictionary of reach station offsets due to junction lengths."""
        return self.walker.reach_distance_modifiers(self.subset_reaches)

    @property
    @lru_cache
    def xs_distance_modifiers(self) -> dict:
        """Get a dictionary mapping the lowest cross-section along a reach to the junction distance below it."""
        modifiers = {}
        for k, v in self.reach_distance_modifiers.items():
            subset = self.source_xs[self.source_xs["river_reach"] == k]
            ds_xs = subset[
                subset["river_station"] == subset["river_station"].min()
            ].iloc[0]
            modifiers[ds_xs["river_reach_rs"]] = v
        return modifiers

    def update_ds_reach_lengths(self, row: pd.Series) -> str:
        """Update ras data by increasing downstream distance at sections above a junction crossing."""
        # Get distance increase
        if row["river_reach_rs"] not in self.xs_distance_modifiers:
            return row["ras_data"]
        modifier = str(self.xs_distance_modifiers[row["river_reach_rs"]])

        # Modify
        ras_data = row["ras_data"]
        lines = ras_data.splitlines()
        data = lines[0].split(",")
        for i in [2, 3, 4]:
            data[i] = modifier
        lines[0] = ",".join(data)
        return "\n".join(lines) + "\n"

    def update_xs_names(self, sections: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Update section IDs to auto-incremented."""
        # Log original information
        sections["source_river"] = sections["river"]
        sections["source_reach"] = sections["reach"]
        sections["source_river_station"] = sections["river_station"]

        # Sort by reach path order then by river_station
        sections["river_reach"] = pd.Categorical(
            sections["river_reach"], categories=self.subset_reaches[::-1], ordered=True
        )
        sections = sections.sort_values(by=["river_reach", "river_station"])

        # Update names
        sections["river_station"] = [int(i) for i in range(1, len(sections) + 1)]
        sections["river_reach_rs"] = (
            sections["river"]
            + " "
            + sections["reach"]
            + " "
            + sections["river_station"].astype(str)
        )
        sections["ras_data"] = sections.apply(self.correct_ras_data, axis=1)
        return sections

    def update_structure_names(self, structures: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Update structure IDs to align with sections."""
        # Log original information
        structures["source_river"] = structures["river"]
        structures["source_reach"] = structures["reach"]
        structures["source_river_station"] = structures["river_station"]

        # Update names
        for i in structures.index:
            sections = self.subset_xs[
                (self.subset_xs["source_river"] == structures.loc[i, "source_river"])
                & (self.subset_xs["source_reach"] == structures.loc[i, "source_reach"])
            ]
            structures.loc[i, "river_station"] = (
                sections.iloc[
                    (
                        sections["source_river_station"]
                        > structures.loc[i, "river_station"]
                    ).argmax()
                ]["river_station"]
                - 0.5
            )
        structures["river_reach_rs"] = (
            structures["river"]
            + " "
            + structures["reach"]
            + " "
            + structures["river_station"].astype(str)
        )
        structures["ras_data"] = structures.apply(self.correct_ras_data, axis=1)
        structures = self.rename_river_reach(structures)
        return structures

    @property
    @lru_cache
    def subset_xs(self) -> gpd.GeoDataFrame:
        """Trim source XS to u/s and d/s limits and add all intermediate reaches."""
        sections = self.source_xs[
            self.source_xs["river_reach"].isin(self.subset_reaches)
        ]
        sections = self.trim_reach(sections)
        sections["ras_data"] = sections.apply(self.update_ds_reach_lengths, axis=1)
        sections = self.update_xs_names(sections)
        return sections

    @property
    @lru_cache
    def subset_structures(self) -> gpd.GeoDataFrame:
        """Trim source structures to u/s and d/s limits and add all intermediate reaches."""
        if self.source_structure is None:
            return None

        structures = self.source_structure[
            self.source_structure["river_reach"].isin(self.subset_reaches)
        ]
        structures = self.trim_reach(structures)

        if len(structures) == 0:
            return None
        else:
            structures = self.update_structure_names(structures)
            return structures

    @property
    @lru_cache
    def subset_river(self) -> gpd.GeoDataFrame:
        """Trim source centerline to u/s and d/s limits and add all intermediate reaches."""
        # Make a new joined centerline
        coords = []
        for river_reach in self.subset_reaches:
            tmp_river = self.source_river[
                self.source_river["river_reach"] == river_reach
            ]
            coords.extend(tmp_river.iloc[0]["geometry"].coords)
        tmp_line = LineString(coords)

        # Subset the river gdf to reverse cross-sections
        rivers = self.source_river[
            self.source_river["river_reach"].isin(self.subset_reaches)
        ]
        tmp_xs = fix_reversed_xs(self.subset_xs, rivers)

        # clip the joined centerline
        tmp_line = clip_ras_centerline(tmp_line, tmp_xs, 2)
        return gpd.GeoDataFrame(
            {"geometry": [tmp_line], "river": [self.nwm_id], "reach": [self.nwm_id]},
            geometry="geometry",
            crs=self.crs,
        )

    @property
    @lru_cache
    def subset_gdfs(self) -> dict:
        """Subset the cross sections, structues, and river geometry for a given NWM reach."""
        subset_gdfs = {}

        # Cross-sections
        subset_gdfs["XS"] = self.subset_xs
        if len(subset_gdfs["XS"]) <= 1:  # check if only 1 cross section for nwm_reach
            err_string = f"Sub model for {self.nwm_id} would have {len(subset_gdfs['XS'])} cross-sections but is not tagged as eclipsed. Skipping."
            logging.warning(err_string)
            raise SingleXSModel(err_string)

        # River centerlines
        subset_gdfs["River"] = self.subset_river

        # Structures
        if self.subset_structures is not None:
            subset_gdfs["Structure"] = self.subset_structures
            subset_gdfs["Structure"] = self.rename_river_reach(subset_gdfs["Structure"])

        # Rename rivers
        for i in subset_gdfs:
            subset_gdfs[i] = self.rename_river_reach(subset_gdfs[i])

        return subset_gdfs

    @property
    def ripple_xs(self) -> gpd.GeoDataFrame:
        """Subset cross sections based on NWM reach."""
        return self.subset_gdfs["XS"]

    @property
    def ripple_river(self) -> gpd.GeoDataFrame:
        """Subset river geometry based on NWM reach."""
        return self.subset_gdfs["River"]

    @property
    def ripple_structure(self) -> gpd.GeoDataFrame:
        """Subset structures based on NWM reach."""
        return self.subset_gdfs["Structure"]

    @property
    def ripple_gpkg_file(self) -> str:
        """Return the path to the new geopackage."""
        return self.nwm_reach_model.ras_gpkg_file

    @property
    def nwm_reach_model(self) -> NwmReachModel:
        """Return the new NWM reach model object."""
        return NwmReachModel(self.dst_project_dir)

    @property
    def min_flow(self) -> float:
        """Extract the min flow from the cross sections."""
        if "flows" in self.ripple_xs.columns:
            return (
                self.ripple_xs["flows"]
                .str.split("\n", expand=True)
                .astype(float)
                .min()
                .min()
                * self.min_flow_multiplier_ras
            )
        else:
            logging.warning(
                f"no flows specified in source model gpkg for {self.nwm_id}"
            )
            return 10000000000000

    @property
    def max_flow(self) -> float:
        """Extract the max flow from the cross sections."""
        if "flows" in self.ripple_xs.columns:
            return (
                self.ripple_xs["flows"]
                .str.split("\n", expand=True)
                .astype(float)
                .max()
                .max()
                * self.max_flow_multiplier_ras
            )
        else:
            logging.warning(
                f"no flows specified in source model gpkg for {self.nwm_id}"
            )
            return 0

    @property
    def crs(self):
        """Extract the CRS from the cross sections."""
        return self.source_xs.crs

    def trim_reach(self, sections: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Trim a XS gdf to the u/s and d/s limits."""
        # Clip upper end
        us_filter = (sections["river_reach"] == self.us_river_reach) & (
            sections["river_station"] > self.us_rs
        )
        sections = sections[~us_filter]

        # Clip lower end
        ds_filter = (sections["river_reach"] == self.ds_river_reach) & (
            sections["river_station"] < self.ds_rs
        )
        sections = sections[~ds_filter]

        return sections

    def write_ripple_gpkg(self) -> None:
        """Write the subsetted geopackage to the destination project directory."""
        os.makedirs(self.dst_project_dir, exist_ok=True)

        for layer, gdf in self.subset_gdfs.items():
            # remove lateral structures
            if layer == "Structure":
                if (gdf["type"] == 6).any():
                    logging.warning(
                        "Lateral structures are not currently supported in ripple1d. The lateral structures will be dropped."
                    )
                    gdf = gdf.loc[gdf["type"] != 6, :]

            if gdf.shape[0] > 0:
                gdf.to_file(self.ripple_gpkg_file, layer=layer)
                if layer == "XS":
                    xs = fix_reversed_xs(gdf, self.subset_gdfs["River"])
                    xs.sort_values(by="river_station", inplace=True, ascending=False)
                    xs_concave_hull(xs).to_file(
                        self.ripple_gpkg_file, driver="GPKG", layer="XS_concave_hull"
                    )

    def correct_ras_data(self, row):
        """Make ras_data names consistent with river_station."""
        ras_data = row["ras_data"]
        rs = row["river_station"]

        lines = ras_data.splitlines()
        data = lines[0].split(",")
        if "*" in data[1]:
            data[1] = str(float(rs)) + "*"
            data[1] = data[1].ljust(8)
        else:
            data[1] = str(float(rs)).ljust(8)
        lines[0] = ",".join(data)
        return "\n".join(lines) + "\n"

    def rename_river_reach(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Rename river, reach, and river_reach columns after the nwm reach id."""
        pd.options.mode.copy_on_write = True
        gdf["river"] = self.nwm_id
        gdf["reach"] = self.nwm_id
        gdf["river_reach"] = f"{self.nwm_id.ljust(16)},{self.nwm_id.ljust(16)}"

        return gdf

    def update_ripple1d_parameters(self, rsd: RippleSourceDirectory):
        """Update ripple1d_parameters with results of subsetting."""
        ripple1d_parameters = self.ripple1d_parameters
        ripple1d_parameters["source_model"] = rsd.ras_project_file
        ripple1d_parameters["source_model_metadata"] = rsd.source_model_metadata
        ripple1d_parameters["crs"] = self.crs.to_wkt()
        ripple1d_parameters["version"] = ripple1d.__version__
        if self.ignore_ras_flows:
            ripple1d_parameters["high_flow"] = ripple1d_parameters["high_flow"]
            ripple1d_parameters["low_flow"] = ripple1d_parameters["low_flow"]
        elif self.ignore_nwm_flows:
            ripple1d_parameters["high_flow"] = self.max_flow
            ripple1d_parameters["low_flow"] = self.min_flow
        else:
            ripple1d_parameters["high_flow"] = max(
                [ripple1d_parameters["high_flow"], self.max_flow]
            )
            ripple1d_parameters["low_flow"] = min(
                [ripple1d_parameters["low_flow"], self.min_flow]
            )
        # Validate that RAS has flows when NWM ignored
        if ripple1d_parameters["high_flow"] == self.max_flow:
            ripple1d_parameters["notes"] = [
                "high_flow computed from source model flows"
            ]
        if ripple1d_parameters["low_flow"] == self.min_flow:
            ripple1d_parameters["notes"] = ["low_flow computed from source model flows"]
        return ripple1d_parameters

    def write_ripple1d_parameters(self, ripple1d_parameters: dict):
        """Write ripple1d parameters to json file."""
        with open(
            os.path.join(self.dst_project_dir, f"{self.nwm_id}.ripple1d.json"), "w"
        ) as f:
            json.dump(ripple1d_parameters, f, indent=4)


def extract_submodel(
    source_model_directory: str,
    submodel_directory: str,
    nwm_id: int,
    model_name: str,
    min_flow_multiplier_ras: float = 1,
    max_flow_multiplier_ras: float = 1,
    ignore_ras_flows: bool = False,
    ignore_nwm_flows: bool = False,
):
    """Use ripple conflation data to create a new GPKG from an existing ras geopackage.

    Create a new geopackage with information for a specific NWM reach.  The new geopackage contains layer for the river centerline, cross-sections, and structures.

    Parameters
    ----------
    source_model_directory : str
        The path to the directory containing HEC-RAS project, plan, geometry, and flow files.
    submodel_directory : str
        The path to export submodel HEC-RAS files to.
    nwm_id : int
        The id of the NWM reach to create a submodel for
    model_name : str
        The name of the HEC-RAS model.
    min_flow_multiplier_ras : float
        Number that will be multiplied by the RAS min modeled flow. Default is 1
    max_flow_multiplier_ras : float
        Number that will be multiplied by the RAS max modeled flow. Default is 1
    ignore_ras_flows : bool
        Whether to ignore HEC-RAS min and max flow when defining flow ranges. Default is False
    ignore_nwm_flows : bool
        Whether to ignore NWM min and max flow when defining flow ranges. Default is False

    Returns
    -------
    dict
        Metadata for the submodel

    Raises
    ------
    FileNotFoundError
        Raised when no geopackage is found in the source model directory
    FileNotFoundError
        Raised when no .conflation.json is found in the source model directory
    """
    if ignore_nwm_flows and ignore_ras_flows:
        raise RuntimeError("ignore_nwm_flows and ignore_ras_flows may not both be True")
    if not os.path.exists(source_model_directory):
        raise FileNotFoundError(
            f"cannot find directory for source model {source_model_directory}, please ensure dir exists"
        )
    rsd = RippleSourceDirectory(source_model_directory, model_name)

    logging.info(f"extract_submodel starting for nwm_id {nwm_id}")

    if not rsd.file_exists(rsd.ras_gpkg_file):
        raise FileNotFoundError(
            f"cannot find file ras-geometry file {rsd.ras_gpkg_file}, please ensure file exists"
        )

    if not rsd.file_exists(rsd.conflation_file):
        raise FileNotFoundError(
            f"cannot find conflation file {rsd.conflation_file}, please ensure file exists"
        )

    ripple1d_parameters = rsd.nwm_conflation_parameters(str(nwm_id))
    if ripple1d_parameters["eclipsed"]:
        ripple1d_parameters["messages"] = (
            f"skipping {nwm_id}; no cross sections conflated."
        )
        logging.warning(ripple1d_parameters["messages"])
        gpkg_path = None
        conflation_file = None

    else:
        rgs = RippleGeopackageSubsetter(
            rsd.ras_gpkg_file,
            rsd.conflation_file,
            submodel_directory,
            nwm_id,
            min_flow_multiplier_ras,
            max_flow_multiplier_ras,
            ignore_ras_flows,
            ignore_nwm_flows,
        )
        rgs.write_ripple_gpkg()
        rgs.copy_metadata_to_ripple1d_gpkg()
        ripple1d_parameters = rgs.update_ripple1d_parameters(rsd)
        rgs.write_ripple1d_parameters(ripple1d_parameters)
        gpkg_path = rgs.ripple_gpkg_file
        conflation_file = (
            rsd.conflation_file
        )  # TODO: this gives a different path than write_ripple1d_parameters

    logging.info(f"extract_submodel complete for nwm_id {nwm_id}")
    return {"ripple1d_parameters": conflation_file, "ripple_gpkg_file": gpkg_path}
