Configuration
=============

The runner is configured via a JSON file with two top-level keys:

- ``common``: paths and settings shared across apps
- ``app``: app-specific container arguments, mounts, and options

See :file:`config_example.json` for a complete template.

Common section
--------------

Typical fields:

- ``bids_folder``: path to the BIDS dataset (mounted at ``/bids``)
- ``output_folder``: derivatives output directory (mounted at ``/output``)
- ``tmp_folder``: work directory base; a per-subject folder is created and mounted at ``/tmp``
- ``templateflow_dir``: TemplateFlow cache directory (mounted at ``/templateflow``)
- ``container``: path to the Apptainer image (``.sif``)
- ``optional_folder``: optional extra folder mounted at ``/base``
- ``jobs``: number of parallel jobs

App section
-----------

Typical fields:

- ``analysis_level``: usually ``participant`` (or ``group``)
- ``apptainer_args``: extra Apptainer flags (e.g. ``--containall``, ``--writable-tmpfs``)
- ``options``: arguments passed to the BIDS App inside the container
- ``mounts``: additional bind mounts (list of ``{source, target}``)
- ``output_check``: optional pattern-based output presence check

Example (fMRIPrep-style)
------------------------

.. code-block:: json

   {
     "common": {
       "bids_folder": "/data/bids",
       "output_folder": "/data/derivatives/fmriprep",
       "tmp_folder": "/data/scratch/fmriprep_work",
       "templateflow_dir": "/data/templateflow",
       "container": "/data/local/container/fmriprep/fmriprep_25.2.3.sif",
       "jobs": 4
     },
     "app": {
       "analysis_level": "participant",
       "apptainer_args": ["--containall"],
       "options": [
         "--fs-license-file", "/fs/license.txt",
         "--skip_bids_validation"
       ],
       "mounts": [
         {"source": "/usr/local/freesurfer", "target": "/fs"}
       ]
     }
   }

