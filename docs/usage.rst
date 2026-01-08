Usage
=====

Build an Apptainer image
------------------------

Build from Docker Hub (interactive repository + tag selection):

.. code-block:: bash

   bash ./build_apptainer.sh \
     -o /data/local/container/fmriprep \
     -t /data/local/tmp_big

Build from a Dockerfile:

.. code-block:: bash

   bash ./build_apptainer.sh \
     -d /path/to/Dockerfile \
     -o /data/local/container/custom \
     -t /data/local/tmp_big

Temp cleanup behavior
^^^^^^^^^^^^^^^^^^^^^

- The script creates a per-build temp subfolder under the path you provide with ``-t``.
- On success, that per-build temp folder is deleted.
- To keep it (for debugging), use ``--no-temp-del``:

.. code-block:: bash

   bash ./build_apptainer.sh -o /data/local/container/fmriprep -t /data/local/tmp_big --no-temp-del


Run a BIDS App from a JSON config
--------------------------------

1) Copy the example config and edit paths/options:

.. code-block:: bash

   cp config_example.json config.json

2) Dry-run a pilot subject (recommended):

.. code-block:: bash

   python run_bids_apps.py -c config.json --dry-run --pilot

3) Run for real:

.. code-block:: bash

   python run_bids_apps.py -c config.json

Run specific subjects:

.. code-block:: bash

   python run_bids_apps.py -c config.json --subjects sub-001 sub-002

Enable debug container logs:

.. code-block:: bash

   python run_bids_apps.py -c config.json --debug --subjects sub-001


Validate outputs and reprocess missing subjects
----------------------------------------------

Generate a missing-subjects report:

.. code-block:: bash

   python check_app_output.py /data/bids /data/derivatives \
     --output-json missing_subjects.json

Re-run missing subjects from a report (optionally filter by pipeline):

.. code-block:: bash

   python run_bids_apps.py -c config.json --reprocess-from-json missing_subjects.json

.. code-block:: bash

   python run_bids_apps.py -c config.json --reprocess-from-json missing_subjects.json --pipeline fmriprep


Notes on dry-run validation
---------------------------

In ``--dry-run`` mode, the runner performs a fast validation that avoids starting the BIDS App itself.
If needed, you can change the validation timeout via:

.. code-block:: bash

   export BIDS_RUNNER_VALIDATE_TIMEOUT=30

Graphical Interface
-------------------

A browser-based workflow supplementing the CLI is available via :doc:`gui`. Run `bash gui/start_gui.sh` or `python app_gui.py`, point your browser to `http://localhost:8080`, and use the form to scan containers, load per-app arguments, save configs, and launch `run_bids_apps.py` with the same flags described above. The GUI also tailors the help output from each container, surfaces validation toggles, and streams the runner logs back to the browser for live feedback.

