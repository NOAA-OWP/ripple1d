##########
User Guide
##########

This section provides a high level overview for using ripple1d for production,
including starting the ripple server and submitting jobs.


Installation
------------


:code:`ripple1d` is registered with `PyPI <https://pypi.org/project/ripple1d>`_
and can be installed simply using python's pip package installer. Assuming you
have Python already installed and setup:

   .. code-block:: powershell

      pip install ripple1d


Note that it is highly recommended to create a python `virtual environment
<https://docs.python.org/3/library/venv.html>`_ to install, test, and run
ripple.


Starting the server
-------------------

The utilities of ripple1d are accessed via an API, and the backend server must
be started before any jobs can be processed.

**Start the Ripple Services**:

   .. code-block:: powershell

      ripple1d start --thread_count 5


**Help for the Ripple Services**:

   .. code-block:: powershell

      ripple1d -h

      ripple1d start -h


By default, starting ripple1d will launch 2 terminal windows, one for the Flask
API and the other for the Huey consumer. Server logs are stored in the same
directory where ripple1d was started. For example, if you started ripple1d in
the directory ``C:\Users\user\Desktop``, a new file
``C:\Users\user\Desktop\server-logs.jsonld`` should be created

Verify that ripple is running by checking the status here: http://localhost/ping

.. code-block:: JSON

   {"status" : "healthy"}

A healthy status indicates that ripple1d is running and waiting for jobs.


Index of Endpoints
------------------
Jobs can be submitted using Postman collections, python clients, curl, or any
other software that can communicate via HTTP REST protocol.  The following
endpoints reflect a typical workflow for ripple1d.

.. toctree::
   :maxdepth: 1

   endpoints/ras_to_gpkg
   endpoints/conflate_model
   endpoints/compute_conflation_metrics
   endpoints/extract_submodel
   endpoints/create_ras_terrain
   endpoints/create_model_run_normal_depth
   endpoints/run_incremental_normal_depth
   endpoints/run_known_wse
   endpoints/create_fim_lib

Example Endpoint Query
----------------------

.. code-block:: python

   import json
   import requests

   headers = {"Content-Type": "application/json"}
   url = f"http://localhost/processes/gpkg_from_ras/execution"
   payload = {
         "source_model_directory": 'path/to/ras_dir',
         "crs": 'EPSG:4326',
         "metadata": {
               "some_field": "relevant_data",
               "some_field2": "other data",
         },
      }
   response = requests.post(url, data=json.dumps(payload), headers=headers)


Troubleshooting
----------------

For help troubleshooting, please add an issue on github at `<https://github.com/Dewberry/ripple1d/issues>`_
