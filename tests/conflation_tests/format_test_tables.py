"""Utility script for formatting conflation tables to silence warnings when running tests."""

import geopandas as gpd
import pandas as pd
from pyogrio.errors import DataLayerError
from shapely import LineString, MultiLineString

from tests.conflation_tests.classes import PathManager
from tests.conflation_tests.consts import JUNCTION_RAS_DATA, RIVER_RAS_DATA, TESTS, XS_RAS_DATA


def format_tables(ras_dir_name: str):
    """Format river_reach and river_reach_rs values for all tables."""
    pm = PathManager(ras_dir_name)

    # Correct river layer
    gpkg = gpd.read_file(pm.ras_path, layer="River")
    gpkg["river_reach"] = gpkg.apply(river_reach, axis=1)
    gpkg["ras_data"] = RIVER_RAS_DATA
    gpkg.to_file(pm.ras_path, layer="River")

    # Correct XS layer
    gpkg = gpd.read_file(pm.ras_path, layer="XS")
    gpkg["river_reach"] = gpkg.apply(river_reach, axis=1)
    gpkg["river_reach_rs"] = gpkg.apply(river_reach_rs, axis=1)
    gpkg["ras_data"] = XS_RAS_DATA
    gpkg["thalweg"] = 470.9
    gpkg.to_file(pm.ras_path, layer="XS")

    # Correct junction layer
    try:
        gpkg = gpd.read_file(pm.ras_path, layer="Junction")
        gpkg["ras_data"] = JUNCTION_RAS_DATA
        gpkg.to_file(pm.ras_path, layer="Junction")
    except DataLayerError:
        pass

    # Make NWM snappable
    nwm = gpd.read_parquet(pm.nwm_path)
    if nwm.geom_type[0] != "MultiLineString":
        nwm["geometry"] = nwm["geometry"].apply(lambda line: MultiLineString([line]))
    nwm["f100year"] = 100.0
    nwm["high_flow_threshold"] = 10.0
    nwm["stream_order"] = 2
    nwm.loc[~nwm["ID"].isin(nwm["to_id"].to_list()), "stream_order"] = 1
    nwm.to_parquet(pm.nwm_path, write_covering_bbox=True)


def river_reach(row: pd.Series) -> str:
    return f"{row['river'].ljust(16)},{row['reach'].ljust(16)}"


def river_reach_rs(row: pd.Series) -> str:
    return f"{row['river']} {row['reach']} {row['river_station']}"


if __name__ == "__main__":
    for ras_dir_name in TESTS:
        format_tables(ras_dir_name)
