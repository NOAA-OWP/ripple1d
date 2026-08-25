"""Conflation Metrics."""

import json
import logging
import os
import sqlite3
import traceback

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
from shapely import Geometry, LineString, MultiLineString, MultiPoint, Point
from shapely.ops import linemerge

from ripple1d.consts import HYDROFABRIC_CRS, METERS_PER_FOOT
from ripple1d.data_model import XS
from ripple1d.ops.subset_gpkg import RippleGeopackageSubsetter
from ripple1d.utils.ripple_utils import fix_reversed_xs, xs_concave_hull


class ConflationMetrics:
    """Calculate metrics for a cross section."""

    def __init__(
        self,
        xs_gdf: gpd.GeoDataFrame,
        river_gdf: gpd.GeoDataFrame,
        hull_gdf,
        network_reach: LineString,
        network_reach_plus_ds_reach: LineString,
        network_id: str,
    ):
        self.xs_gdf = xs_gdf
        self.river_gdf = river_gdf
        self.hull_gdf = hull_gdf
        self.network_reach = network_reach
        self.network_reach_plus_ds_reach = network_reach_plus_ds_reach
        self.crs = xs_gdf.crs
        self.network_id = network_id

    def populate_station_elevation(self, row: pd.Series) -> dict:
        """Populate the station elevation for a cross section."""
        xs = XS(row.ras_data.splitlines(), row.river_reach, row.river, row.reach, self.crs)
        return dict(xs.station_elevation_points)

    def populate_thalweg_station(self, row: pd.Series) -> str:
        """Populate the thalweg station for a cross section."""
        df = pd.DataFrame(row["station_elevation"], index=["elevation"]).T
        return df.loc[df["elevation"] == row.thalweg].index[0]

    def thalweg_metrics(self, xs_gdf: gpd.GeoDataFrame) -> dict:
        """Calculate the distance between the thalweg point and the network intersection point."""
        try:
            xs_gdf["station_elevation"] = xs_gdf.apply(lambda row: self.populate_station_elevation(row), axis=1)
            xs_gdf["thalweg_station"] = xs_gdf.apply(lambda row: self.populate_thalweg_station(row), axis=1)
            xs_gdf["thalweg_point"] = xs_gdf.apply(
                lambda row: row.geometry.interpolate(row["thalweg_station"] * METERS_PER_FOOT), axis=1
            )

            xs_gdf["ras_intersection_point"] = None
            for _, r in self.river_gdf.iterrows():
                xs_gdf.loc[xs_gdf["river_reach"] == r["river_reach"], "ras_intersection_point"] = xs_gdf.loc[
                    xs_gdf["river_reach"] == r["river_reach"], :
                ].apply(lambda row: r.geometry.intersection(row.geometry), axis=1)

            xs_gdf["network_intersection_point"] = xs_gdf.apply(
                lambda row: self.network_reach_plus_ds_reach.intersection(row.geometry), axis=1
            )

            xs_gdf["centerline_offset"] = xs_gdf.apply(
                lambda row: row["ras_intersection_point"].distance(row["network_intersection_point"]), axis=1
            )
            xs_gdf["thalweg_offset"] = xs_gdf.apply(
                lambda row: row["thalweg_point"].distance(row["network_intersection_point"]), axis=1
            )

            return {
                "centerline_offset": xs_gdf["centerline_offset"].describe().round().fillna(-9999).astype(int).to_dict(),
                "thalweg_offset": xs_gdf["thalweg_offset"].describe().round().fillna(-9999).astype(int).to_dict(),
            }
        except Exception as e:
            logging.error(f"network id: {self.network_id} | Error: {e}")
            logging.error(f"network id: {self.network_id} | Traceback: {traceback.format_exc()}")

    def clean_length(self, length: float, meters_to_feet: bool = True):
        """Clean the length."""
        if length == 0:
            length = None
        elif np.isnan(length):
            length = None
        else:
            if meters_to_feet:
                length = int(length / METERS_PER_FOOT)
            else:
                length = int(length)
        return length

    def compute_station(self, river: LineString, xs: gpd.GeoDataFrame):
        """Compute the station for a river reach."""
        xs["intersection_point"] = xs.apply(lambda row: river.intersection(row.geometry), axis=1)

        xs["station"] = xs.apply(
            lambda row: (
                river.project(row["intersection_point"]) if isinstance(row["intersection_point"], Point) else np.nan
            ),
            axis=1,
        )
        return self.clean_length(xs["station"].max() - xs["station"].min())

    @property
    def river_line(self):
        """Get the geomtry for the river centerline."""
        return self.river_gdf.iloc[0].geometry

    def us_ds_xs(self, xs_gdf: gpd.GeoDataFrame):
        """Get the most upstream and downstream cross sections."""
        if len(xs_gdf["river_reach"].unique()) > 1:
            raise ValueError("Cross sections must all be on the same river reach.")

        return xs_gdf[
            (xs_gdf["river_station"] == xs_gdf["river_station"].max())
            | (xs_gdf["river_station"] == xs_gdf["river_station"].min())
        ]

    def length_metrics(self, xs_gdf: gpd.GeoDataFrame) -> dict:
        """Calculate the reach length between cross sections along the ras river line and the network reach."""
        try:
            xs = self.us_ds_xs(xs_gdf)
            network_length = self.compute_station(self.network_reach_plus_ds_reach, xs)
            ras_length = self.compute_station(self.river_line, xs)

            if ras_length is None or network_length is None:
                network_ras_ratio = None
            else:
                network_ras_ratio = round(float(network_length / ras_length), 2)

            return {
                "ras": ras_length,
                "network": network_length,
                "network_to_ras_ratio": network_ras_ratio,
            }
        except Exception as e:
            logging.error(f"network id: {self.network_id} | Error: {e}")
            logging.error(f"network id: {self.network_id} | Traceback: {traceback.format_exc()}")

    # def parrallel_reaches(self, network_reaches: gpd.GeoDataFrame) -> dict:
    #     """Calculate the overlap between the network reach and the cross sections."""
    #     if network_reaches.empty:
    #         return []
    #     overlaps = network_reaches[network_reaches.intersects(self.xs_gdf.union_all())]
    #     if overlaps.empty:
    #         return []
    #     overlaps["overlap"] = overlaps.apply(
    #         lambda row: int(
    #             row["geometry"].intersection(xs_concave_hull(self.xs_gdf)["geometry"].iloc[0]).length / METERS_PER_FOOT
    #         ),
    #         axis=1,
    #     )

    #     return [{"id": str(row["ID"]), "overlap": row["overlap"]} for _, row in overlaps.iterrows()]

    def overlapped_reaches(self, to_reaches: gpd.GeoDataFrame) -> dict:
        """Calculate the overlap between the network reach and the cross sections."""
        if to_reaches.empty:
            return []
        reach_ids = []
        geom_name = to_reaches.geometry.name
        for i, row in to_reaches.iterrows():
            if row[geom_name].intersects(self.xs_gdf.union_all()):
                overlap = row[geom_name].intersection(self.hull_gdf["geometry"].iloc[0]).length / METERS_PER_FOOT
                reach_ids.append({"id": str(row["ID"]), "overlap": int(overlap)})
        return reach_ids

    def eclipsed_reaches(self, network_reaches: gpd.GeoDataFrame) -> dict:
        """Calculate the overlap between the network reach and the cross sections."""
        if network_reaches.empty:
            return []
        eclipsed_reaches = network_reaches[network_reaches.covered_by(self.hull_gdf["geometry"].iloc[0])]

        return [str(row["ID"]) for _, row in eclipsed_reaches.iterrows()]

    def compute_coverage_metrics(self, xs_gdf: gpd.GeoDataFrame) -> dict:
        """Calculate the coverage metrics for a set of cross sections."""
        try:
            xs_gdf = xs_gdf[
                (xs_gdf["river_station"] == xs_gdf["river_station"].max())
                | (xs_gdf["river_station"] == xs_gdf["river_station"].min())
            ]
            xs_gdf["intersection_point"] = xs_gdf.apply(
                lambda row: self.network_reach_plus_ds_reach.intersection(row.geometry), axis=1
            )
            xs_gdf["station_percent"] = xs_gdf.apply(
                lambda row: (
                    self.network_reach_plus_ds_reach.project(row["intersection_point"]) / self.network_reach.length
                    if isinstance(row["intersection_point"], Point)
                    else np.nan
                ),
                axis=1,
            )
            return {
                "start": float(round(xs_gdf["station_percent"].min(), 2)),
                "end": min([round(xs_gdf["station_percent"].max(), 2), 1]),
            }
        except Exception as e:
            logging.error(f"network id: {self.network_id} | Error: {e}")
            logging.error(f"network id: {self.network_id} | Traceback: {traceback.format_exc()}")


def compute_conflation_metrics(source_model_directory: str, model_name: str, source_network: dict):
    """Compute metrics for a network reach.

    Parameters
    ----------
    source_model_directory : str
        The path to the directory containing HEC-RAS project, plan, geometry,
        and flow files.
    model_name: str
        The name of the source HEC-RAS model.
    source_network : dict
        Information on the network to conflate

        - **file_name** (str):
            path/to/nwm_network.parquet (required)
        - **type** (str):
            must be 'nwm_hydrofabric' (required)
        - **version** (str):
            optional version number to log
    task_id : str, optional
        Task ID to use for logging, by default ""

    Returns
    -------
    dict
        Dictionary containing metrics describing model network match

    Notes
    -----
    The compute_conflation_metrics endpoint uses the information gathered in
    conflation to summarize how well a NWM reach and HEC-RAS model section
    align.  The metrics are broken down into three categories: xs, lengths, and
    coverage.

    * **xs.**  These metrics quantify the degree of alignment between the NWM
      reach centerline and the HEC-RAS model.  The metrics below are measured
      at HEC-RAS cross-section and summary statistics are reported in the
      conflation metrics output.

      * centerline_offset measures the straightline distance between RAS
        centerline and NWM reach line
      * thalweg_offset measures the straightline distance between lowest point
        along each RAS section and NWM reach line
    * **lengths.** These metrics assess centerline length differences between
      HEC-RAS and the NWM reaches.

      * ras is the distance along the RAS centerline between upstream and
        downstream cross-section
      * network is the distance along the NWM reach between upstream and
        downstream cross-section
      * network_to_ras_ratio is the network length divided by ras length
    * **coverage.** These metrics quantify the portion of the NWM reach between
      the upstream and downstream cross-section.

      * start is the ratio of NWM reach length that occurs u/s of the upstream
        cross-section
      * end is the ratio of NWM reach length that occurs u/s of the downstream
        cross-section

    compute_conflation_metrics also reports overlapped reaches and eclipsed
    reaches.  Overlapped_reaches is a list of reaches that are intersected by
    the most d/s cross-section of this model.  Eclipsed_reaches is a list of
    NWM reaches that are contained within the concave hull of the
    cross-sections.
    """
    logging.info(f"compute_conflation_metrics starting")
    network_pq_path = source_network["file_name"]
    src_gpkg_path = os.path.join(source_model_directory, f"{model_name}.gpkg")
    conflation_json = os.path.join(source_model_directory, f"{model_name}.conflation.json")
    conflation_parameters = json.load(open(conflation_json))

    for network_id in conflation_parameters["reaches"].keys():
        try:
            if conflation_parameters["reaches"][network_id]["eclipsed"] == True:
                continue

            rgs = RippleGeopackageSubsetter(src_gpkg_path, conflation_json, "", network_id)
            layers = {}
            for layer, gdf in rgs.subset_gdfs.items():
                layers[layer] = gdf.to_crs(HYDROFABRIC_CRS)

            network_reaches = gpd.read_parquet(network_pq_path, bbox=layers["XS"].total_bounds)
            network_reach = linemerge(network_reaches.loc[network_reaches["ID"] == int(network_id)].geometry.iloc[0])
            network_reach_plus_ds_reach = combine_reaches(network_reaches, network_id)

            cm = ConflationMetrics(
                fix_reversed_xs(layers["XS"], layers["River"]),
                layers["River"],
                rgs.ripple_xs_concave_hull.to_crs(HYDROFABRIC_CRS),
                network_reach,
                network_reach_plus_ds_reach,
                network_id,
            )

            metrics = {
                "xs": cm.thalweg_metrics(layers["XS"]),
                "lengths": cm.length_metrics(layers["XS"]),
                "coverage": cm.compute_coverage_metrics(layers["XS"]),
            }

            to_id = conflation_parameters["reaches"][network_id]["network_to_id"]
            if to_id in conflation_parameters["reaches"].keys():
                next_to_id = int(conflation_parameters["reaches"][to_id]["network_to_id"])
            else:
                next_to_id = None

            overlapped_reaches = cm.overlapped_reaches(
                network_reaches[network_reaches["ID"].isin([int(to_id), next_to_id])]
            )
            eclipsed_reaches = get_eclipsed_reaches(conflation_parameters, network_id)

            conflation_parameters["reaches"][network_id].update({"metrics": metrics})
            conflation_parameters["reaches"][network_id].update({"overlapped_reaches": overlapped_reaches})
            conflation_parameters["reaches"][network_id].update({"eclipsed_reaches": eclipsed_reaches})
            conflation_parameters["metadata"]["length_units"] = "feet"
            conflation_parameters["metadata"]["flow_units"] = "cfs"
        except Exception as e:
            logging.error(f"network id: {network_id} | Error: {e}")
            logging.error(f"network id: {network_id} | Traceback: {traceback.format_exc()}")
            conflation_parameters["reaches"][network_id].update({"metrics": {}})
    with open(conflation_json, "w") as f:
        f.write(json.dumps(conflation_parameters, indent=4))

    logging.info(f"compute_conflation_metrics complete")
    return conflation_parameters


def combine_reaches(network_reaches: gpd.GeoDataFrame, network_id: str) -> LineString:
    """Combine network reaches."""
    reach = network_reaches.loc[network_reaches["ID"] == int(network_id), :]
    to_reach = network_reaches.loc[network_reaches["ID"] == int(reach["to_id"].iloc[0]), :]

    if to_reach.empty:
        return linemerge(reach.geometry.iloc[0])
    else:
        next_to_reach = network_reaches.loc[network_reaches["ID"] == int(to_reach["to_id"].iloc[0]), :]
        if next_to_reach.empty:
            return linemerge(MultiLineString([linemerge(reach.geometry.iloc[0]), linemerge(to_reach.geometry.iloc[0])]))
        else:
            return linemerge(
                MultiLineString(
                    [
                        linemerge(reach.geometry.iloc[0]),
                        linemerge(to_reach.geometry.iloc[0]),
                        linemerge(next_to_reach.geometry.iloc[0]),
                    ]
                )
            )


def get_eclipsed_reaches(conflation_parameters, network_id):
    """Walk the network to find all eclipsed reaches until the next non-eclipsed reach."""
    walking = True
    next_ds = conflation_parameters["reaches"][network_id]["network_to_id"]
    eclipsed = []
    while walking:
        if not next_ds in conflation_parameters["reaches"]:
            break
        if not conflation_parameters["reaches"][next_ds]["eclipsed"]:
            break
        else:
            eclipsed.append(next_ds)
            next_ds = conflation_parameters["reaches"][next_ds]["network_to_id"]
    return eclipsed
