# scripts/patches/

Local patches for third-party packages installed into the gitignored
`.datalad-slurm-venv`. Since that venv is a plain (non-editable) install,
any fix applied by hand to its site-packages is lost on rebuild -- these
patch files exist so the fix can be reapplied.

Run after (re)creating the venv:

```
scripts/patches/apply_datalad_slurm_patches.sh
```

## datalad_slurm_finish_db_removal_order.patch

`datalad-slurm`'s `finish_cmd()` removed a job's open-jobs DB entry
*before* actually saving/committing its outputs. Any interruption of the
finish job between those two steps (SLURM wallclock timeout, `scancel`,
OOM, node failure) left the outputs staged but uncommitted, with no way
to resume -- a resubmitted `slurm-finish <job_id>` can no longer find the
job in its own database. This caused two real incidents: 144 manually
hand-recovered "Recovery commit" entries in the freesurfer derivatives
history (2026-07-24 to 07-27) and job 5505212 losing track of a qsiprep
retry's output (2026-07-27). Reported upstream:
https://github.com/knuedd/datalad-slurm/issues/97

The patch moves the DB removal to after the save completes, so an
interrupted finish stays resumable.
