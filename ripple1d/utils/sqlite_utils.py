"""Utils for working with sqlite databases."""

import json
import os
import sqlite3

import pandas as pd

from ripple1d.ras import RasManager


def create_db_and_table(db_name: str, table_name: str):
    """Create sqlite database and table."""
    os.makedirs(os.path.dirname(os.path.abspath(db_name)), exist_ok=True)
    sql_query = f"""
        CREATE TABLE {table_name}(
            reach_id INTEGER,
            ds_depth REAL,
            ds_wse REAL,
            us_flow INTEGER,
            us_depth REAL,
            us_wse REAL,
            boundary_condition TEXT, -- [kwse, nd]
            plan_suffix TEXT,
            map_exist BOOL CHECK(map_exist IN (0, 1)),
            xs_overtopped BOOL CHECK(xs_overtopped IN (0, 1)),
            UNIQUE(reach_id, us_flow, ds_wse, boundary_condition, plan_suffix)
        )
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute(sql_query)
    conn.commit()
    conn.close()


def insert_data(
    db_name: str, table_name: str, data: pd.DataFrame, plan_suffix: str, missing_grids: list, boundary_condition: str
):
    """Insert data into the sqlite database."""
    conn = sqlite3.connect(db_name)
    c = conn.cursor()

    for row in data.itertuples():
        if boundary_condition == "kwse":
            if f'f_{int(row.us_flow)}-z_{str(row.ds_wse).replace(".","_")}' in missing_grids:
                map_exist = 0
            else:
                map_exist = 1
        elif boundary_condition == "nd":
            if str(int(row.us_flow)) in missing_grids:
                map_exist = 0
            else:
                map_exist = 1
        else:
            raise ValueError(
                f"Could not detemine boundary condition type for {boundary_condition}; expected kwse or nd"
            )

        c.execute(
            f"""
            INSERT OR REPLACE INTO {table_name} (reach_id, ds_depth, ds_wse, us_flow, us_depth, us_wse, boundary_condition,  plan_suffix, map_exist,xs_overtopped)
            VALUES ( ?, ?, ?, ?, ?, ?, ?, ?, ?,?)
        """,
            (
                int(row.reach_id),
                round(float(row.ds_depth), 1),
                round(float(row.ds_wse), 1),
                round(float(row.us_flow), 1),
                round(float(row.us_depth), 1),
                round(float(row.us_wse), 1),
                str(boundary_condition),
                str(plan_suffix),
                str(map_exist),
                str(row.xs_overtopped),
            ),
        )

    conn.commit()
    conn.close()


def parse_stage_flow(wses: pd.DataFrame) -> pd.DataFrame:
    """Parse flow and control by stage from profile names."""
    wses_t = wses.T
    wses_t.reset_index(inplace=True)
    wses_t[["us_flow", "ds_wse"]] = wses_t["index"].str.split("-", n=1, expand=True)
    wses_t.drop(columns="index", inplace=True)
    wses_t["us_flow"] = wses_t["us_flow"].str.lstrip("f_").astype(float)
    wses_t["ds_wse"] = wses_t["ds_wse"].str.lstrip("z_")
    wses_t["ds_wse"] = wses_t["ds_wse"].str.replace("_", ".").astype(float)

    return wses_t


def rs_float(river_reach_rs: str, token: str = " ") -> str:
    """Convert river station to float for a given river_reach_rs."""
    parts = river_reach_rs.split(token)
    parts[-1] = str(float(parts[-1]))
    return " ".join(parts)


def zero_depth_to_sqlite(
    rm: RasManager,
    plan_name: str,
    plan_suffix: str,
    nwm_id: str,
    missing_grids: list,
    database_path: str,
    table_name: str,
):
    """Export zero depth (normal depth) results to sqlite."""
    # set the plan
    rm.plan = rm.plans[plan_name]

    profile_name_map = json.loads(rm.plans[plan_name].flow.description)
    # read in flow/wse
    wses, flows = rm.plan.read_plan_results(profile_name_map)

    # get river-reach-rs
    us_river_reach_rs = rm.plan.geom.rivers[nwm_id][nwm_id].us_xs.river_reach_rs_str
    ds_river_reach_rs = rm.plan.geom.rivers[nwm_id][nwm_id].ds_xs.river_reach_rs_str

    wses_t = wses.T
    wses_t["us_flow"] = wses_t.index
    wses_t["ds_depth"] = 0
    df = wses_t.loc[:, [us_river_reach_rs, "us_flow", "ds_depth"]]
    df.rename(columns={us_river_reach_rs: "us_wse"}, inplace=True)

    # convert elevation to stage for upstream cross section
    us_thalweg = rm.plan.geom.rivers[nwm_id][nwm_id].us_xs.thalweg
    df["us_depth"] = df["us_wse"] - us_thalweg

    # convert elevation to stage for downstream cross section
    ds_df = wses_t.loc[:, [ds_river_reach_rs, "us_flow", "ds_depth"]]
    ds_df.rename(columns={ds_river_reach_rs: "us_wse"}, inplace=True)
    ds_thalweg = rm.plan.geom.rivers[nwm_id][nwm_id].ds_xs.thalweg

    df["ds_wse"] = ds_df["us_wse"]
    df["ds_depth"] = df["ds_wse"] - ds_thalweg

    # add control id
    df["reach_id"] = [nwm_id] * len(df)

    df["xs_overtopped"] = check_overtopping(rm, wses)
    insert_data(database_path, table_name, df, plan_suffix, missing_grids, boundary_condition="nd")


def check_overtopping(rm: RasManager, wses: pd.DataFrame):
    """Check if the crossection was overopped."""
    gdf = rm.current_plan.geom.xs_gdf
    gdf["river_reach_rs"] = gdf.apply(lambda x: rs_float(x.river_reach_rs), axis=1)
    if not (gdf.river_reach_rs_str.values == wses.index.values).any():
        raise ValueError(
            f"Cannot safely check overtopping of cross sections. The river_reach_rs of the geometry xs_gdf does not match the river_reach_rs from the plan hdf. The order of the cross sections may have changed."
        )
    return wses.gt(gdf["overtop_elevation"].values, axis=0).any().astype(int)


def scenarios_to_sqlite(
    rm: RasManager,
    plan_name: str,
    plan_suffix: str,
    nwm_id: str,
    missing_grids: list,
    database_path: str,
    table_name: str,
):
    """Export known water surface elevation scenarios to sqlite."""
    # set the plan
    if plan_name not in rm.plans:
        return
    rm.plan = rm.plans[plan_name]

    profile_name_map = json.loads(rm.plans[plan_name].flow.description)
    # read in flow/wse
    wses, flows = rm.plan.read_plan_results(profile_name_map)
    wses_t = wses.T
    wses_t["xs_overtopped"] = check_overtopping(rm, wses)

    # parse applied stage and flow from profile names
    wses_ = parse_stage_flow(wses_t.T)

    # get river-reach-rs id
    river_reach_rs = rm.plan.geom.rivers[nwm_id][nwm_id].us_xs.river_reach_rs_str

    # get subset of results for this cross section
    df = wses_[["us_flow", "ds_wse", "xs_overtopped", river_reach_rs]].copy()

    # rename columns
    df.rename(columns={river_reach_rs: "us_wse"}, inplace=True)

    # add control id
    df["reach_id"] = [nwm_id] * len(df)

    # convert elevation to stage
    thalweg = rm.plan.geom.rivers[nwm_id][nwm_id].us_xs.thalweg
    df["us_depth"] = df["us_wse"] - thalweg

    thalweg = rm.plan.geom.rivers[nwm_id][nwm_id].ds_xs.thalweg
    df["ds_depth"] = df["ds_wse"] - thalweg

    insert_data(database_path, table_name, df, plan_suffix, missing_grids, boundary_condition="kwse")


def create_non_spatial_table(gpkg_path: str, metadata: dict) -> None:
    """Create the metadata table in the geopackage."""
    with sqlite3.connect(gpkg_path) as conn:
        string = ""
        curs = conn.cursor()
        curs.execute("DROP TABLE IF Exists metadata")
        curs.execute(f"CREATE TABLE IF NOT EXISTS metadata (key, value);")
        curs.close()

    with sqlite3.connect(gpkg_path) as conn:
        curs = conn.cursor()
        keys, vals = "", ""
        for key, val in metadata.items():

            if val:
                keys += f"{key},"
                vals += f"{val.replace(',','_')}, "
                curs.execute(f"INSERT INTO metadata (key,value) values (?,?);", (key, val))

        curs.execute("COMMIT;")
        curs.close()
    return None


def create_terrain_agreement_db(out_path: str):
    """Initialize agreement database."""
    with sqlite3.connect(out_path) as con:
        cur = con.cursor()
        cur.execute(
            "CREATE TABLE model_metrics (avg_inundation_overlap REAL, avg_flow_area_overlap REAL, avg_top_width_agreement REAL, avg_flow_area_agreement REAL, avg_hydraulic_radius_agreement REAL, avg_r_squared REAL, avg_spectral_angle REAL, avg_spectral_correlation REAL, avg_correlation REAL, avg_max_cross_correlation REAL, avg_thalweg_elevation_difference REAL)"
        )
        cur.execute(
            "CREATE TABLE xs_metrics (xs_id TEXT PRIMARY KEY, avg_inundation_overlap REAL, avg_flow_area_overlap REAL, avg_top_width_agreement REAL, avg_flow_area_agreement REAL, avg_hydraulic_radius_agreement REAL, r_squared REAL, spectral_angle REAL, spectral_correlation REAL, correlation REAL, max_cross_correlation REAL, thalweg_elevation_difference REAL, max_el_residuals_mean REAL, max_el_residuals_std REAL, max_el_residuals_max REAL, max_el_residuals_min REAL, max_el_residuals_p_25 REAL, max_el_residuals_p_50 REAL, max_el_residuals_p_75 REAL, max_el_residuals_rmse REAL, max_el_residuals_normalized_rmse REAL)"
        )
        cur.execute(
            "CREATE TABLE xs_elevation_metrics (elevation REAL, xs_id TEXT, inundation_overlap REAL, flow_area_overlap REAL, top_width_agreement REAL, flow_area_agreement REAL, hydraulic_radius_agreement REAL, residuals_mean REAL, residuals_std REAL, residuals_max REAL, residuals_min REAL, residuals_p_25 REAL, residuals_p_50 REAL, residuals_p_75 REAL, residuals_rmse REAL, residuals_normalized_rmse REAL, PRIMARY KEY (xs_id, elevation), FOREIGN KEY (xs_id) REFERENCES xs_metrics (xs_id))"
        )
    con.close()


def agreement_dict_sql_prep(d: dict, table: str):
    """Preprocess a dict to be easily instered with insertmany()."""
    for k in d:
        subdicts = [k2 for k2 in d[k] if isinstance(d[k][k2], dict)]
        for sd in subdicts:
            for r in d[k][sd]:
                d[k][f"{sd}_{r}"] = d[k][sd][r]
            del d[k][sd]
    tmp_keys = d[k].keys()
    insertable = list(d.values())
    stm = f"INSERT INTO {table} ({', '.join(tmp_keys)}) values ({', '.join([':' + k for k in tmp_keys])})"
    return stm, insertable


def export_terrain_agreement_metrics_to_db(out_path: str, metrics: dict):
    """Export terrain agreement dict to a sqlite database."""
    create_terrain_agreement_db(out_path)

    try:
        with sqlite3.connect(out_path) as con:
            # Add model summary
            cur = con.cursor()
            tmp_dict = metrics["model_metrics"]
            stm = f"INSERT INTO model_metrics ({', '.join(tmp_dict.keys())}) values ({', '.join([':' + k for k in tmp_dict.keys()])})"
            cur.execute(stm, tmp_dict)

            # Add cross-section summaries
            tmp_dict = {}
            for k in metrics["xs_metrics"]:
                tmp_dict[k] = metrics["xs_metrics"][k]["summary"]
                tmp_dict[k]["xs_id"] = k
            stm, insertable = agreement_dict_sql_prep(tmp_dict, "xs_metrics")
            cur.executemany(stm, insertable)

            # Add elevation metrics
            tmp_dict = {}
            for k in metrics["xs_metrics"]:
                for k2 in metrics["xs_metrics"][k]["xs_elevation_metrics"]:
                    combo_k = f"{k}-{k2}"
                    tmp_dict[combo_k] = metrics["xs_metrics"][k]["xs_elevation_metrics"][k2]
                    tmp_dict[combo_k]["xs_id"] = k
                    tmp_dict[combo_k]["elevation"] = k2
            stm, insertable = agreement_dict_sql_prep(tmp_dict, "xs_elevation_metrics")
            cur.executemany(stm, insertable)
        con.close()
    except Exception as e:
        con.rollback()
        con.close()
        raise e
