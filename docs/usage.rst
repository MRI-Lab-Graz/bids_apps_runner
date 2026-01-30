Usage
=====

GUI workflow (recommended)
-------------------------

1. Start the GUI:

.. code-block:: bash

   python prism_app_runner.py

2. Create or load a project in Projects.
3. Configure paths, container, and app options in Run App.
4. Save the project (writes project.json).
5. Optional: edit SLURM settings in HPC -> Advanced.
6. Run from Run App.

CLI workflow
------------

Run directly from a JSON config:

.. code-block:: bash

   python scripts/prism_runner.py -c configs/config.json

Common flags:

.. code-block:: bash

   --dry-run
   --subjects sub-001 sub-002
   --force
   --debug
   --log-level DEBUG
   --jobs 4

Output validation
-----------------

.. code-block:: bash

   python scripts/check_app_output.py /path/to/bids /path/to/derivatives --output-json missing.json

Container build
---------------

.. code-block:: bash

   ./scripts/build_apptainer.sh -o /path/to/containers/fmriprep.sif -t /tmp/apptainer_build

Container option locking
------------------------

Options are auto-loaded from the container the first time.
After you save the project, the options are locked and will not be overwritten unless the container path changes.

