Configuration
=============

Project configuration is stored in project.json. The GUI reads and writes this file.
The CLI can also run directly from JSON.

Top-level keys
--------------

- common: paths and container settings
- app: app-specific arguments and mounts
- hpc (optional): SLURM settings (Advanced section in GUI)
- datalad (optional): DataLad inputs/outputs

Common section
--------------

Typical fields:

- bids_folder: path to BIDS dataset
- output_folder: derivatives output directory
- tmp_folder: working directory
- templateflow_dir: TemplateFlow cache
- container_engine: apptainer or docker
- container: path to .sif or Docker image
- container_locked: true after the first save to prevent auto-reloading options
- jobs: number of parallel jobs

App section
-----------

Typical fields:

- analysis_level: participant or group
- apptainer_args: extra Apptainer flags
- options: arguments passed to the app inside the container
- mounts: additional bind mounts (list of source/target pairs)

HPC section (SLURM)
-------------------

Editable in the GUI under Advanced: SLURM Settings:

- partition, time, mem, cpus
- job_name, output_pattern, error_pattern
- modules (list)
- environment (JSON map)
- monitor_jobs (boolean)

Example
-------

.. code-block:: json

   {
     "common": {
       "bids_folder": "/data/bids",
       "output_folder": "/data/derivatives/fmriprep",
       "tmp_folder": "/data/scratch/fmriprep_work",
       "templateflow_dir": "/data/templateflow",
       "container_engine": "apptainer",
       "container": "/data/local/container/fmriprep/fmriprep_25.2.3.sif",
       "container_locked": true,
       "jobs": 4
     },
     "app": {
       "analysis_level": "participant",
       "options": ["--fs-license-file", "/fs/license.txt"],
       "mounts": [{"source": "/usr/local/freesurfer", "target": "/fs"}]
     },
     "hpc": {
       "partition": "compute",
       "time": "24:00:00",
       "mem": "32G",
       "cpus": 8,
       "job_name": "fmriprep",
       "output_pattern": "slurm-%j.out",
       "error_pattern": "slurm-%j.err",
       "modules": ["apptainer/1.2.0"],
       "environment": {"APPTAINER_CACHEDIR": "/tmp/.apptainer"},
       "monitor_jobs": true
     }
   }

