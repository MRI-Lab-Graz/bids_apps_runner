/**
 * Cohort panel: multi-subject SLURM array job submission via the
 * datalad-slurm workflow (scripts/submit_bids_cohort.sh /
 * scripts/hpc_datalad_runner.py), driven through the /cohort/* routes in
 * gui/gui_cohort_routes.py.
 *
 * Extracted from the inline <script> in templates/index.html -- this was
 * the whole trailing subsystem of that file, self-contained (only reads
 * globals already established as cross-file-visible: lastProjectId,
 * currentPipelineId, logHPC, escapeHtml, _requireSavedProjectForHpc). Same
 * plain classic <script src="...">, global-scope pattern as
 * static/js/project_loader.js and static/js/readiness_panel.js.
 */

let _cohortJobId = null;
let _cohortPollTimer = null;
let _cohortLastLogLen = 0;

function toggleCohortPanel() {
    const body = document.getElementById('cohortPanelBody');
    const icon = document.getElementById('cohortPanelIcon');
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    icon.style.transform = open ? '' : 'rotate(180deg)';
    if (!open) checkCohortOpenJobs();
}

let _cohortOpenJobsCheck = { checking: false, error: null, openJobs: [] };

async function checkCohortOpenJobs() {
    const panel = document.getElementById('cohortJobsPanel');
    const badge = document.getElementById('cohortJobsBadge');
    const detail = document.getElementById('cohortJobsDetail');
    const closeBtn = document.getElementById('cohortCloseJobsBtn');
    if (!panel || !lastProjectId) { if (panel) panel.style.display = 'none'; return; }

    panel.style.display = 'block';
    panel.className = 'alert alert-secondary py-2 px-3 small mb-3';
    _cohortOpenJobsCheck.checking = true;
    badge.className = 'badge bg-secondary';
    badge.textContent = 'Checking...';
    detail.textContent = '';
    closeBtn.style.display = 'none';

    const maxConcurrent = document.getElementById('cohort_max_concurrent').value || 50;
    const params = new URLSearchParams({
        project_id: lastProjectId,
        pipeline_id: currentPipelineId || '',
        max_concurrent: maxConcurrent,
    });

    let data;
    try {
        const resp = await fetch(`/cohort/check_open_jobs?${params}`);
        data = await resp.json();
    } catch (err) {
        _cohortOpenJobsCheck = { checking: false, error: 'Request failed: ' + err, openJobs: [] };
        badge.className = 'badge bg-danger';
        badge.textContent = 'Error';
        detail.textContent = _cohortOpenJobsCheck.error;
        return;
    }

    _cohortOpenJobsCheck.checking = false;
    _cohortOpenJobsCheck.error = data.error || null;
    _cohortOpenJobsCheck.openJobs = data.open_jobs || [];

    if (data.error) {
        badge.className = 'badge bg-secondary';
        badge.textContent = 'Unknown';
        detail.textContent = data.error;
        return;
    }

    if (_cohortOpenJobsCheck.openJobs.length === 0) {
        panel.className = 'alert alert-success py-2 px-3 small mb-3';
        badge.className = 'badge bg-success';
        badge.textContent = 'Clear';
        detail.textContent = 'No stuck datalad-slurm jobs for this dataset.';
        return;
    }

    const closeable = _cohortOpenJobsCheck.openJobs.filter(
        (j) => j.status !== 'RUNNING' && j.status !== 'PENDING'
    );
    panel.className = 'alert alert-warning py-2 px-3 small mb-3';
    badge.className = 'badge bg-warning text-dark';
    badge.textContent = `${_cohortOpenJobsCheck.openJobs.length} open`;
    detail.innerHTML = 'Unfinished datalad-slurm job(s) will block Submit for overlapping subjects: '
        + _cohortOpenJobsCheck.openJobs.map(
            (j) => `<code>${escapeHtml(j.job_id)}</code> (${escapeHtml(j.status)})`
        ).join(', ');
    if (closeable.length > 0) {
        closeBtn.style.display = 'inline-flex';
    }
}

async function closeCohortOpenJobs() {
    const closeBtn = document.getElementById('cohortCloseJobsBtn');
    closeBtn.disabled = true;
    const maxConcurrent = document.getElementById('cohort_max_concurrent').value || 50;
    try {
        const resp = await fetch('/cohort/close_open_jobs', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                project_id: lastProjectId,
                pipeline_id: currentPipelineId || '',
                max_concurrent: maxConcurrent,
            }),
        });
        const data = await resp.json();
        logHPC(data.output || (data.ok ? 'Closed failed datalad-slurm jobs.' : 'Failed to close jobs.'), !data.ok);
    } catch (err) {
        logHPC('Request failed: ' + err, true);
    } finally {
        closeBtn.disabled = false;
        checkCohortOpenJobs();
    }
}

function _basenameOf(path) {
    const trimmed = (path || '').replace(/\/+$/, '');
    const parts = trimmed.split('/');
    return parts[parts.length - 1] || '';
}

function updateCohortDatasetDisplay(bidsFolderOverride) {
    const display = document.getElementById('cohortDatasetIdDisplay');
    if (!display) return;
    const bidsFolder = bidsFolderOverride !== undefined
        ? bidsFolderOverride
        : (document.getElementById('bids_folder')?.value || '');
    display.value = bidsFolder ? _basenameOf(bidsFolder) : '(load a project with a BIDS Dataset Folder set)';
}

async function previewCohortConfig() {
    const pre = document.getElementById('cohortConfigPreview');
    if (!_requireSavedProjectForHpc('previewing the cohort config')) return;

    const maxConcurrent = document.getElementById('cohort_max_concurrent').value || 50;
    const params = new URLSearchParams({
        project_id: lastProjectId,
        pipeline_id: currentPipelineId || '',
        max_concurrent: maxConcurrent,
    });

    try {
        const resp = await fetch(`/cohort/preview_config?${params}`);
        const data = await resp.json();
        if (!resp.ok || data.error) {
            pre.style.display = 'block';
            pre.textContent = 'Error: ' + (data.error || 'Failed to derive config.');
            return;
        }
        pre.style.display = 'block';
        pre.textContent = JSON.stringify(data.config, null, 2);
    } catch (err) {
        pre.style.display = 'block';
        pre.textContent = 'Request failed: ' + err.message;
    }
}

function _cohortSetBusy(busy, command) {
    ['cohortSetupBtn','cohortSubmitBtn','cohortStatusBtn','cohortSubmitSubregionsBtn'].forEach(id => {
        document.getElementById(id).disabled = busy;
    });
    document.getElementById('cohortCancelBtn').style.display = busy ? 'inline-flex' : 'none';
    const badge = document.getElementById('cohortBadge');
    const msg   = document.getElementById('cohortStatusMsg');
    document.getElementById('cohortStatusBadge').style.display = 'block';
    if (busy) {
        badge.className = 'badge bg-warning text-dark';
        badge.textContent = 'running';
        msg.textContent   = command;
    }
}

function _cohortSetDone(status, command) {
    ['cohortSetupBtn','cohortSubmitBtn','cohortStatusBtn','cohortSubmitSubregionsBtn'].forEach(id => {
        document.getElementById(id).disabled = false;
    });
    document.getElementById('cohortCancelBtn').style.display = 'none';
    const badge = document.getElementById('cohortBadge');
    const msg   = document.getElementById('cohortStatusMsg');
    badge.className = status === 'completed'
        ? 'badge bg-success' : status === 'cancelled'
        ? 'badge bg-secondary' : 'badge bg-danger';
    badge.textContent = status;
    msg.textContent   = `${command} finished`;
    checkCohortOpenJobs();
}

async function runCohort(command) {
    if (!_requireSavedProjectForHpc(`running cohort ${command}`)) return;

    const dryRun = document.getElementById('cohortDryRun').checked;
    // Pre-existing checkbox that was never actually wired to a request --
    // submit_bids_cohort.sh has supported --resume all along, the GUI just
    // never sent it.
    const resumeEl = document.getElementById('cohortResume');
    const resume = !!(resumeEl && resumeEl.checked);
    const maxConcurrent = document.getElementById('cohort_max_concurrent').value || 50;
    // Pilot mode: submit_bids_cohort.sh (--pilot) narrows the array job down
    // to one randomly-chosen subject instead of the whole cohort -- lets you
    // validate the full container/mount/DataLad-provenance path (the actual
    // "submit" command, not a dry-run) before committing to every subject.
    // Meaningful for "submit" (narrows the array) and "status" (reads the
    // separate pilot_submission_*.log instead of the real cohort's, so
    // checking status right after a pilot-only submit doesn't fall through
    // to whatever unrelated dataset's real submission happens to be most
    // recent). cmd_setup() never looks at $PILOT at all -- Setup always
    // clones/prefetches the whole dataset regardless -- so it's excluded
    // here too, consistent with the request/log actually sent for it.
    const pilotEl = document.getElementById('cohortPilot');
    const pilot = command !== 'setup' && !!(pilotEl && pilotEl.checked);

    _cohortLastLogLen = 0;
    _cohortSetBusy(true, command);
    logHPC(`Starting cohort ${command}${dryRun ? ' (dry-run)' : ''}${resume ? ' (resume)' : ''}${pilot ? ' (PILOT: 1 random subject)' : ''}…`);

    let resp;
    try {
        resp = await fetch('/cohort/run', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                command,
                project_id: lastProjectId,
                pipeline_id: currentPipelineId || '',
                dry_run: dryRun,
                resume,
                max_concurrent: maxConcurrent,
                pilot,
            }),
        });
    } catch (err) {
        logHPC(`Network error: ${err}`, true);
        _cohortSetDone('failed', command);
        return;
    }

    const data = await resp.json();
    if (!resp.ok || data.error) {
        logHPC(data.error || 'Unknown error', true);
        _cohortSetDone('failed', command);
        return;
    }

    _cohortJobId = data.job_id;
    logHPC(`Job started: ${_cohortJobId}`);
    _cohortPollTimer = setInterval(() => _cohortPoll(command), 1500);
}

async function _cohortPoll(command) {
    if (!_cohortJobId) return;
    let data;
    try {
        const r = await fetch(`/cohort/job_status?job_id=${_cohortJobId}`);
        data = await r.json();
    } catch { return; }

    const log = data.log_tail || '';
    if (log.length > _cohortLastLogLen) {
        const newText = log.slice(_cohortLastLogLen);
        const out = document.getElementById('hpcLogOutput');
        out.textContent += newText;
        out.scrollTop = out.scrollHeight;
        _cohortLastLogLen = log.length;
    }

    if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
        clearInterval(_cohortPollTimer);
        _cohortPollTimer = null;
        _cohortSetDone(data.status, command);
        logHPC(`Cohort ${command} ${data.status}` + (data.returncode != null ? ` (exit ${data.returncode})` : ''));
    }
}

async function cancelCohort() {
    if (!_cohortJobId) return;
    await fetch('/cohort/cancel', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ job_id: _cohortJobId }),
    });
    if (_cohortPollTimer) { clearInterval(_cohortPollTimer); _cohortPollTimer = null; }
    _cohortSetDone('cancelled', 'cancel');
    logHPC('Cohort job cancelled');
}
