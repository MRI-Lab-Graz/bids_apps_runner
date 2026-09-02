import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "submit_bids_cohort.sh"


def _source() -> str:
    return SCRIPT.read_text()


class TestFinishJobsCommitFailedJobs:
    """Regression test for the close-open-jobs data-loss bug (2026-09-01,
    megastudy_openneuro mriqc): a finish job dependency-chained with
    `afterany` runs `datalad slurm-finish` even when some array elements
    TIMEOUT'd rather than COMPLETED. Without --commit-failed-jobs,
    datalad_slurm's finish_cmd() takes the branch that removes the job's
    DB entry and returns WITHOUT ever calling Save on the declared
    outputs (see .datalad-slurm-venv/.../datalad_slurm/finish.py), so any
    real output a timed-out array element produced is silently abandoned
    as untracked. --commit-failed-jobs falls through to the same Save
    call the normal all-COMPLETED path uses, so it must be present on
    every `slurm-finish --slurm-job-id` invocation in the generated
    finish-job templates, not just the close_open_jobs GUI route.
    """

    def test_array_finish_job_uses_commit_failed_jobs(self):
        source = _source()
        match = re.search(
            r'\$DATALAD_BIN"\s+slurm-finish\s+.*--slurm-job-id "\$\{job_id\}"[^\n]*',
            source,
        )
        assert match, "could not find the array finish job's slurm-finish call"
        assert "--commit-failed-jobs" in match.group(0)

    def test_subregion_finish_job_uses_commit_failed_jobs(self):
        source = _source()
        match = re.search(
            r'\$DATALAD_BIN"\s+slurm-finish\s+.*--slurm-job-id "\$\{subregion_job_id\}"[^\n]*',
            source,
        )
        assert match, "could not find the subregion finish job's slurm-finish call"
        assert "--commit-failed-jobs" in match.group(0)


class TestFinishJobsRunIncrementalSaveFirst:
    """The hybrid finish-job design: slurm-finish's own Save call has been
    the single point of failure across every incident this session dug up
    (the --close-failed-jobs early-return, a >12h monolithic save blowing
    wallclock, and project 134's whole-dataset unlock-with-no-matching-
    -relock). Running scripts/incremental_datalad_save.sh first -- per-
    subject, checkpointed, resumable -- means slurm-finish's own Save call
    then runs against an already-clean tree (a fast no-op), so it's left
    doing only what it's actually good at: the provenance commit and
    closing the datalad-slurm DB entry. This keeps slurm-schedule's
    conflicting-outputs guard and the run-record, while removing the
    monolithic-save failure mode.
    """

    def test_array_finish_job_runs_incremental_save_before_slurm_finish(self):
        source = _source()
        incr_pos = source.find("incremental_datalad_save.sh", source.find("schedule_one_batch() {"))
        finish_pos = source.find('--slurm-job-id "${job_id}"')
        assert incr_pos != -1, "array finish job template does not call incremental_datalad_save.sh"
        assert incr_pos < finish_pos, "incremental_datalad_save.sh must run BEFORE slurm-finish"

    def test_subregion_finish_job_runs_incremental_save_before_slurm_finish(self):
        source = _source()
        incr_pos = source.find(
            "incremental_datalad_save.sh", source.find("schedule_one_subregion_batch() {")
        )
        finish_pos = source.find('--slurm-job-id "${subregion_job_id}"')
        assert incr_pos != -1, "subregion finish job template does not call incremental_datalad_save.sh"
        assert incr_pos < finish_pos, "incremental_datalad_save.sh must run BEFORE slurm-finish"

    def test_array_finish_incremental_save_uses_the_array_subject_list(self):
        source = _source()
        array_region = source[source.find("schedule_one_batch() {") :]
        match = re.search(r'incremental_datalad_save\.sh"[^\n]*', array_region)
        assert match, "could not find the array finish job's incremental_datalad_save.sh invocation"
        assert '-d "${output_clone}"' in match.group(0)
        assert '-s "${subj_list}"' in match.group(0)


class TestUncommittedCheckIsShared:
    """The post-finish 'uncommitted change(s) remain' check was hand-copied
    verbatim into both the array-finish and subregion-finish heredocs.
    push_verification_block() (same file) already establishes the pattern
    for sharing a verification snippet across both templates via
    $(push_verification_block) -- the uncommitted check should follow it
    instead of existing as two independently-maintained copies.
    """

    def test_uncommitted_check_message_defined_once(self):
        source = _source()
        occurrences = source.count("uncommitted change(s) remain after slurm-finish")
        assert occurrences == 1, (
            f"expected the uncommitted-check message to be defined once in a "
            f"shared block, found it {occurrences} times"
        )

    def test_both_templates_reference_the_shared_block(self):
        source = _source()
        assert source.count("$(uncommitted_check_block)") == 2
