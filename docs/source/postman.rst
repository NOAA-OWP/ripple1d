Postman collection
==================

For reference and documentation of the API, please open the postman collection for the version of ripple1d

v0.11.0: This version renames an endpoint. **This is a breaking change; there is no backwards-compatible alias.**
 - `create_rating_curves_db` is now `create_scenarios_db`. The records it writes are computed model scenarios
   (a discharge, and for `kwse` plans a downstream stage, with the resulting water surface elevations), not
   rating curves. The output table is renamed from `rating_curves` to `scenarios` to match, and the result key
   returned by the endpoint is renamed from `rating_curve_database` to `scenario_database`.

`v.0.10.4: <https://github.com/Dewberry/ripple1d/blob/77987266ec360536d07c6ff1e8546d15a210426e/ripple1d/api/postman_collection.json>`_ This version contains a new optional args for create_fim_lib and conflate_model:
 - `cog` (boolean)  added to  `create_fim_lib`.  This is a boolean indicating if the depth grids should be cloud optimized geotiffs or not.
 - `min_flow_multiplier` (float)  added to  `conflate_model`.  This is the number that will be multiplied by the NWM "high flow threshold" to define the low_flow value in the conflation json.
 - `max_flow_multiplier` (float)  added to  `conflate_model`.  This is the number that will be multiplied by the NWM 100-year flow to define the high_flow value in the conflation json.
 - `min_flow_multiplier_ras` (float)  added to  `extract_submodel`.  This is the number that will be multiplied by the RAS min modeled flow. Default is 1.
 - `max_flow_multiplier_ras` (float)  added to  `extract_submodel`.  This is the number that will be multiplied by the RAS max modeled flow. Default is 1.
 - `ignore_ras_flows` (bool)  added to  `extract_submodel`.  Whether to ignore HEC-RAS min and max flow when defining flow ranges. Default is False.
 - `ignore_nwm_flows` (bool)  added to  `extract_submodel`.  Whether to ignore NWM min and max flow when defining flow ranges. Default is False.

`v.0.10.1-v.0.10.3: <https://github.com/Dewberry/ripple1d/blob/58a873910f0dfe312f7d674793470389836aac5b/ripple1d/api/postman_collection.json>`_ This version contains new args for the conflate_model and compute_conflation_metrics endpoints :
 - `model_name` (str)  added to  `extract_submodel`.  This is the name of the source model. Example: Red River.prj -> Red River (model_name)
 - `terrain_agreement_ignore_error` (bool)  added to  `create_ras_terrain`.  If true, this will log and ignore any errors encountered in the terrain agreement calculation process.

`v.0.10.0: <https://github.com/Dewberry/ripple1d/blob/93cf22cf11791d59820635be6c02327b39912b49/ripple1d/api/postman_collection.json>`_ This version contains new args for the conflate_model and compute_conflation_metrics endpoints :
 - `model_name` (str)  added to  `conflate_model`.  This is the name of the source model. Example: Red River.prj -> Red River (model_name)
 - `model_name` (str)  added to  `compute_conflation_metrics`.  This is the name of the source model.Example: Red River.prj -> Red River (model_name)

`v0.8.0-v.0.8.3: <https://github.com/Dewberry/ripple1d/blob/39089e932b1052e1b708a84eefff47f1973759c5/ripple1d/api/postman_collection.json>`_ This beta version contains new args for the create_ras_terrain endpoint:
 - `terrain_agreement_resolution` (float)  added to  `create_ras_terrain`.  This is the maximum distance allowed between the vertices used to calculate terrain agreement metrics.  It is in the units of the HEC-RAS model.
 - `f` (json or html) added to jobs.  Default value is json.  Determines the response format of the endpoint.

`v0.7.0: <https://github.com/Dewberry/ripple1d/blob/ac8596f4c7d4a42f189ba4591803dfd6f94887ca/ripple1d/api/postman_collection.json>`_ This beta version contains:
 new endpoints:
   - `create_rating_curves_db`: creates rating curve using results from `run_known_wse` and `run_incremental_normal_depth` results
   - `jobs`: added endpoints to view job `results`, `metadata`, and `logs`

 new args:
  - `write_depth_grids` (bool)  added to  `run_known_wse` and `run_incremental_normal_depth` endpoints

`v0.6.0-v0.6.3: <https://github.com/Dewberry/ripple1d/blob/4fe2488f9d73aec08121a5c3034bf2445d0258e6/ripple1d/api/postman_collection.json>`_ This beta version contains new args for the create_fim_lib endpoint:
 - `library_directory`: Specifies the output directory for the FIM grids and database.
 - `cleanup`: Boolean indicating if the ras HEC-RAS output grids should be deleted or not.


`v0.5.0: <https://github.com/Dewberry/ripple1d/blob/3c90acc3fa212fde9c9b361dc3b907beaca17919/ripple1d/api/postman_collection.json>`_ This beta version contains new endpoints:
  - `geom_to_gpkg`: Extract the data from a model source dirctory to a gepoackage.
  - `conflate`: Conflate all reaches from the NWM network corresponding to the source model.
  - `conflation_metrics`: Apply conflation metrics for a conflated source model.


`v0.4.1-v0.4.2: <https://github.com/Dewberry/ripple1d/blob/666190451620e033e8783241c020d2cde21660c9/ripple1d/api/postman_collection.json>`_ This beta version contains the endpoints included in the first production testing release. Note that the following variables should be set in the postman environment


.. code-block:: YAML

    postman variables:

    - key: url
      value: localhost
      type: string
      description: The url of the ripple1d API

    - key: source_model_directory
      value: "~\\repos\\ripple1d\\tests\\ras-data\\Baxter"
      type: string
      description: The source model directory (this needs to point to local directory where the source HEC-RAS model is stored)

    - key: submodels_base_directory
      value: "~\\repos\\ripple1d\\tests\\ras-data\\Baxter\\submodels"
      type: string
      description: The base directory for the submodels (this needs to point to local directory where submodels generated by ripple1d are stored)

    - key: nwm_reach_id
      value: '2823932'
      type: string
      description: The NWM reach id for the model (the default value included is for the Baxter model)

    - key: jobID
      value: ''
      type: string
      description: The job id for the model run (this value is generated by the API)

`v0.3.11: <https://github.com/Dewberry/ripple1d/blob/1b1488c1cdff88bbbe85333af52eff2bc3570d75/api/postman_collection.json>`_ This version contains the first experimental endpoints included in the ripple1d API.
