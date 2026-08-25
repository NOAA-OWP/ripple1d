create_ras_terrain
##################

**URL:** ``/processes/create_ras_terrain/execution``

**Method:** ``POST``

**Description:**

.. autofunction:: ripple1d.ops.ras_terrain.create_ras_terrain
    :no-index:


********************
Terrain Agreement
********************

Terrain agreement metrics are written to either a json file with suffix
".terrain_agreement.json" or a sqlite database with suffix
".terrain_agreement.db" in the model directory.  The json will have the
following schema:

.. code-block::

        {
        "type": "object",
        "properties": {
            "xs": {
            "type": "object",
            "additional_properties": {
                "type": "object",
                "properties": {
                "elevation": {
                    "type": "object",
                    "properties": {
                    "inundation_overlap": { "type": "number" },
                    "flow_area_overlap": { "type": "number" },
                    "top_width_agreement": { "type": "number" },
                    "flow_area_agreement": { "type": "number" },
                    "hydraulic_radius_agreement": { "type": "number" },
                    "residuals": {
                        "type": "object",
                        "properties": {
                        "mean": { "type": "number" },
                        "std": { "type": "number" },
                        "max": { "type": "number" },
                        "min": { "type": "number" },
                        "p_25": { "type": "number" },
                        "p_50": { "type": "number" },
                        "p_75": { "type": "number" },
                        "rmse": { "type": "number" },
                        "normalized_rmse": { "type": "number" }
                        }
                    }
                    }
                },
                "summary": {
                    "type": "object",
                    "properties": {
                    "inundation_overlap": { "type": "number" },
                    "flow_area_overlap": { "type": "number" },
                    "top_width_agreement": { "type": "number" },
                    "flow_area_agreement": { "type": "number" },
                    "hydraulic_radius_agreement": { "type": "number" },
                    "residuals": {
                        "type": "object",
                        "properties": {
                        "mean": { "type": "number" },
                        "std": { "type": "number" },
                        "max": { "type": "number" },
                        "min": { "type": "number" },
                        "p_25": { "type": "number" },
                        "p_50": { "type": "number" },
                        "p_75": { "type": "number" },
                        "rmse": { "type": "number" },
                        "normalized_rmse": { "type": "number" }
                        }
                    },
                    "r_squared": { "type": "number" },
                    "spectral_angle": { "type": "number" },
                    "spectral_correlation": { "type": "number" },
                    "correlation": { "type": "number" },
                    "max_cross_correlation": { "type": "number" },
                    "thalweg_elevation_difference": { "type": "number" }
                    }
                }
                }
            }
            },
            "summary": {
            "type": "object",
            "properties": {
                "inundation_overlap": { "type": "number" },
                "flow_area_overlap": { "type": "number" },
                "top_width_agreement": { "type": "number" },
                "flow_area_agreement": { "type": "number" },
                "hydraulic_radius_agreement": { "type": "number" },
                "r_squared": { "type": "number" },
                "spectral_angle": { "type": "number" },
                "spectral_correlation": { "type": "number" },
                "correlation": { "type": "number" },
                "max_cross_correlation": { "type": "number" },
                "thalweg_elevation_difference": { "type": "number" }
            }
            }
        }
        }


and the database will have the following schema:

.. table:: SQLite database schema
   :widths: 5 20 20 1 700

+-------+-----------------------------------------+----------------------+----------+----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| type  | name                                    | tbl_name             | rootpage | sql                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
+=======+=========================================+======================+==========+==================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================+
| table | model_metrics                           | model_metrics        | 2        | CREATE TABLE model_metrics (avg_inundation_overlap REAL, avg_flow_area_overlap REAL, avg_top_width_agreement REAL, avg_flow_area_agreement REAL, avg_hydraulic_radius_agreement REAL, avg_r_squared REAL, avg_spectral_angle REAL, avg_spectral_correlation REAL, avg_correlation REAL, avg_max_cross_correlation REAL, avg_thalweg_elevation_difference REAL)                                                                                                                                                                                                                                                                   |
+-------+-----------------------------------------+----------------------+----------+----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| table | xs_metrics                              | xs_metrics           | 3        | CREATE TABLE xs_metrics (xs_id TEXT PRIMARY KEY, avg_inundation_overlap REAL, avg_flow_area_overlap REAL, avg_top_width_agreement REAL, avg_flow_area_agreement REAL, avg_hydraulic_radius_agreement REAL, r_squared REAL, spectral_angle REAL, spectral_correlation REAL, correlation REAL, max_cross_correlation REAL, thalweg_elevation_difference REAL, max_el_residuals_mean REAL, max_el_residuals_std REAL, max_el_residuals_max REAL, max_el_residuals_min REAL, max_el_residuals_p_25 REAL, max_el_residuals_p_50 REAL, max_el_residuals_p_75 REAL, max_el_residuals_rmse REAL, max_el_residuals_normalized_rmse REAL)  |
+-------+-----------------------------------------+----------------------+----------+----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| table | xs_elevation_metrics                    | xs_elevation_metrics | 5        | CREATE TABLE xs_elevation_metrics (elevation REAL, xs_id TEXT, inundation_overlap REAL, flow_area_overlap REAL, top_width_agreement REAL, flow_area_agreement REAL, hydraulic_radius_agreement REAL, residuals_mean REAL, residuals_std REAL, residuals_max REAL, residuals_min REAL, residuals_p_25 REAL, residuals_p_50 REAL, residuals_p_75 REAL, residuals_rmse REAL, residuals_normalized_rmse REAL, PRIMARY KEY (xs_id, elevation), FOREIGN KEY (xs_id) REFERENCES xs_metrics (xs_id))                                                                                                                                     |
+-------+-----------------------------------------+----------------------+----------+----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
