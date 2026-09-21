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


class TestFinishJobsGuaranteeAProvenanceCommit:
    """Verified against the installed datalad source (datalad/core/local/
    save.py:619-633): once incremental_datalad_save.sh has committed
    everything, the tree is fully clean, and slurm-finish's own
    Save.__call__ on an empty `paths_by_ds` yields status='notneeded' and
    creates ZERO commits -- the `[DATALAD SLURM RUN]` provenance record
    this call exists to write would silently never be created. datalad_slurm's
    remove_from_database() (finish.py:561-572) does a hard DELETE with no
    archival, so that job's entire history would be lost from git, not
    merely reduced -- contradicting this whole design's point of keeping
    slurm-finish around for its provenance commit. A marker file written
    just before slurm-finish (within the -o . output scope, after the
    incremental save) guarantees Save always has a real, non-empty diff to
    attach the provenance message to.
    """

    def test_array_finish_writes_a_marker_before_slurm_finish(self):
        source = _source()
        array_region = source[source.find("schedule_one_batch() {") :]
        marker_pos = array_region.find("finish-marker-")
        incr_pos = array_region.find("incremental_datalad_save.sh")
        finish_pos = array_region.find('--slurm-job-id "${job_id}"')
        assert marker_pos != -1, "array finish job template does not write a provenance marker"
        assert incr_pos < marker_pos < finish_pos, (
            "marker must be written after the incremental save and before slurm-finish"
        )

    def test_subregion_finish_writes_a_marker_before_slurm_finish(self):
        source = _source()
        region = source[source.find("schedule_one_subregion_batch() {") :]
        marker_pos = region.find("finish-marker-")
        incr_pos = region.find("incremental_datalad_save.sh")
        finish_pos = region.find('--slurm-job-id "${subregion_job_id}"')
        assert marker_pos != -1, "subregion finish job template does not write a provenance marker"
        assert incr_pos < marker_pos < finish_pos, (
            "marker must be written after the incremental save and before slurm-finish"
        )


class TestGeneratedScriptPathsAreAppNamespaced:
    """Regression test for the cross-app script-collision bug (2026-09-15,
    megastudy_openneuro fmriprep): configs/generated/'s array/finish script
    filenames were keyed only by dataset+batch, with no app name in them.
    Since MRIQC and fmriprep share the same configs/ dir (so the same
    "generated" scripts_dir) and can be pointed at the same dataset, a
    fmriprep submission's --resume saw MRIQC's already-generated
    ${ds}_bids_array_batch01.sh sitting at that path and silently reused it
    -- 5 array jobs ran the mriqc container, mislabeled as fmriprep, before
    being caught and cancelled. Including ${APP_NAME} in every generated
    array/finish script filename makes that collision structurally
    impossible: two different apps now always write to different paths.
    Deliberately NOT applied to subject-list filenames (_batch_subj_list,
    ${SUBJ_LISTS_DIR}/${DS}_subjects*.txt) -- unlike the scripts, sharing a
    subject list across apps run on the same dataset is normal/intended.
    """

    def test_cmd_submit_unbatched_paths_include_app_name(self):
        source = _source()
        assert 'local array_script="${scripts_dir}/${DS}_${APP_NAME}_bids_array${subj_list_suffix}.sh"' in source
        assert 'local finish_script="${scripts_dir}/${DS}_${APP_NAME}_bids_finish${subj_list_suffix}.sh"' in source

    def test_cmd_submit_batch01_paths_include_app_name(self):
        source = _source()
        assert '"${scripts_dir}/${DS}_${APP_NAME}_bids_array_batch01.sh"' in source
        assert '"${scripts_dir}/${DS}_${APP_NAME}_bids_finish_batch01.sh"' in source

    def test_continue_batch_paths_include_app_name(self):
        source = _source()
        region = source[source.find("cmd_continue_batch() {") :]
        assert 'local array_script="${scripts_dir}/${ds}_${APP_NAME}_bids_array_batch${padded_idx}.sh"' in region
        assert 'local finish_script="${scripts_dir}/${ds}_${APP_NAME}_bids_finish_batch${padded_idx}.sh"' in region

    def test_continue_batch_resolves_config_before_using_app_name(self):
        # APP_NAME is set by resolve_config -- cmd_continue_batch must call
        # it before building paths that reference ${APP_NAME}, or the
        # variable would be empty/stale.
        source = _source()
        region = source[source.find("cmd_continue_batch() {") :]
        resolve_pos = region.find("resolve_config")
        array_script_pos = region.find("local array_script=")
        assert resolve_pos != -1 and array_script_pos != -1
        assert resolve_pos < array_script_pos


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


class TestWholeDatasetCompleteNotification:
    """2026-09-21: notify_ntfy.sh's transport was fixed (a routinely
    unreachable ntfy.sh backend IP with no retry was silently swallowing
    every notification), which surfaced that "whole dataset done" had no
    signal of its own -- every batch's finish job fires the same generic
    "Cohort finish OK" message whether or not more batches are still
    coming, so completion looked identical to any other batch. The
    no-next-batch branch of continue_block must fire a visibly distinct
    notification instead.
    """

    def test_final_batch_fires_a_distinct_completion_notification(self):
        source = _source()
        assert "Dataset COMPLETE:" in source

    def test_completion_notification_only_in_the_no_next_batch_branch(self):
        source = _source()
        # the main (non-subregion) continue_block -- the second of the two
        # `local continue_block=""` declarations in this file
        region = source[source.find("local continue_block=\"\"", source.find("local continue_block=\"\"") + 1):]
        if_has_next_pos = region.find("if $has_next; then")
        else_pos = region.find("\n    else\n")
        complete_pos = region.find("Dataset COMPLETE:")
        assert if_has_next_pos != -1 and else_pos != -1 and complete_pos != -1
        # chaining call sits in the `if $has_next` branch, completion notice
        # sits after the `else` (no-next-batch) branch
        assert if_has_next_pos < else_pos < complete_pos

    def test_completion_notification_reports_a_real_subject_count(self):
        source = _source()
        region = source[source.find("Dataset COMPLETE:") - 200 : source.find("Dataset COMPLETE:") + 50]
        assert "TOTAL_SUBJECTS=" in region
        assert "wc -l" in region
