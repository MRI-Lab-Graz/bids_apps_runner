GUI Interface
=============

The GUI is the primary workflow. It edits project.json and provides controls for running, validation, and HPC settings.

Launch
------

.. code-block:: bash

   python prism_app_runner.py

Open the printed URL (default http://localhost:8080).

Tabs and flow
-------------

Projects
  Create or load a project. Each project stores a project.json file.

Run App
  Set data paths, container, and app options. Options are auto-loaded only once.

HPC
  Advanced SLURM settings are hidden behind an Advanced panel. Power users can edit and save hpc settings.

Check Output
  Run the output validator from the GUI.

Build
  Build Apptainer containers from Docker images.

Container option locking
------------------------

- First time: options are loaded from container help.
- After save: container_locked is set and options are preserved.
- Change container path to re-enable auto-loading.

Logs
----

The console log panel streams output from the most recent run.
