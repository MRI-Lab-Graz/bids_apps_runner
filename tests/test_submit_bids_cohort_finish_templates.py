import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "submit_bids_cohort.sh"


def _source() -> str:
    return SCRIPT.read_text()


def _code_lines(source: str) -> str:
    """Source without comment-only lines (the comments deliberately explain
    the old flow and still name its commands)."""
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


def _call(region: str, marker: str = 'annex_cohort_finish.sh"') -> str:
    """The invocation containing `marker`, including continuation lines (the
    heredoc source escapes the line-continuation backslash as two)."""
    lines = region.splitlines()
    start = next(
        (i for i, l in enumerate(lines) if marker in l and not l.lstrip().startswith("#")),
        None,
    )
    assert start is not None, f"no call containing {marker}"
    end = start
    while lines[end].rstrip().endswith("\\"):
        end += 1
    return "\n".join(lines[start : end + 1])


class TestCohortRunsOnAnnexSlurmNotTheOldFlow:
    """The cohort schedule/finish path used `slurm-schedule -o .` and
    `slurm-finish` (the datalad-slurm extension), then `git status` to check
    for leftovers. All of them compare index against worktree and hang on
    repos with git-annex keys-DB drift (dataset 134;
    docs/superpowers/specs/2026-09-09-annex-slurm-design.md). The generated
    jobs and the submit script itself must use annex-slurm instead.
    """

    def test_no_datalad_slurm_commands_are_executed(self):
        code = _code_lines(_source())
        assert "slurm-schedule" not in code.replace("annex-slurm-schedule", "")
        assert "slurm-finish" not in code.replace("annex-slurm-finish", "")

    def test_finish_jobs_never_run_git_status(self):
        assert "git status" not in _code_lines(_source())

    def test_old_verification_blocks_are_gone(self):
        source = _source()
        assert "uncommitted_check_block" not in source
        assert "push_verification_block" not in source

    def test_array_is_scheduled_via_annex_schedule_batch_with_root_files(self):
        source = _source()
        region = source[source.find("schedule_one_batch() {") :]
        call = _call(region, "annex_schedule_batch \"$output_clone\"")
        assert '"$subj_list" "$array_script"' in call
        assert "--root-files" in call

    def test_subregion_array_is_scheduled_via_annex_schedule_batch(self):
        source = _source()
        region = source[
            source.find("schedule_one_subregion_batch() {") : source.find("schedule_one_batch() {")
        ]
        call = _call(region, "annex_schedule_batch \"$output_clone\"")
        assert '"$timepoint_list" "$subregion_array_script"' in call
        assert '--extra "subregion_results"' in call

    def test_submit_script_has_no_private_copy_of_the_unlock_and_job_id_logic(self):
        code = _code_lines(_source())
        assert "annex-slurm/bin/annex-slurm-schedule" not in code
        executed = "\n".join(l for l in code.splitlines() if "echo" not in l)
        assert re.search(r"--parsable\b", executed) is None  # sacct's --parsable2 is fine


class TestFinishJobsRunAnnexCohortFinish:
    """The finish jobs commit through scripts/annex_cohort_finish.sh, which
    lists each subject's unannexed files with `find` and hands them to
    annex-slurm-finish, then re-scans: done means nothing is left unannexed.
    """

    def test_array_finish_commits_the_batch_subjects_plus_shared_outputs(self):
        source = _source()
        call = _call(source[source.find("schedule_one_batch() {") :])
        assert '-d "${output_clone}"' in call
        assert '-s "${subj_list}"' in call
        assert "--root-files" in call
        assert '--extra ".slurm_logs/${ds}"' in call

    def test_subregion_finish_commits_the_batch_timepoints_plus_results(self):
        source = _source()
        call = _call(
            source[source.find("schedule_one_subregion_batch() {") : source.find("schedule_one_batch() {")]
        )
        assert '-d "${output_clone}"' in call
        assert '-s "${timepoint_list}"' in call
        assert '--extra "subregion_results"' in call
        assert '--extra ".slurm_logs/${ds}"' in call

    def test_finish_jobs_do_not_depend_on_the_old_provenance_marker(self):
        assert "finish-marker-" not in _code_lines(_source())


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
