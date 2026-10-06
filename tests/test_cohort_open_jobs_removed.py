"""annex-slurm keeps no bookkeeping DB, so there are no "open jobs" to list or
close: SLURM's own squeue/sacct is the only job state. The two routes that
drove the old extension's `slurm-finish --list-open-jobs` / `--commit-failed-jobs`
(and could hang on repos with keys-DB drift) are gone."""

import prism_app_runner


def test_open_jobs_routes_no_longer_exist():
    rules = {rule.rule for rule in prism_app_runner.app.url_map.iter_rules()}
    assert "/cohort/check_open_jobs" not in rules
    assert "/cohort/close_open_jobs" not in rules
