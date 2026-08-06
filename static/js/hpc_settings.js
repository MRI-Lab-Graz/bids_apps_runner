// Advanced: SLURM Settings form <-> project config round-trip.
//
// Extracted out of the inline <script> in templates/index.html per
// CLAUDE.md's "chip away at the monoliths" rule (touched while adding
// batch_size). Reads/writes still-inline helpers (applyHpcPreset,
// _validateHpcTimeFormat, _validateHpcMemFormat, _parseHpcEnvironmentInput,
// validateHpcEmailField, scheduleHpcPreflightRefresh, logHPC) and
// cohort_panel.js's updateCohortDatasetDisplay() -- safe because classic
// <script src> tags share one global scope with the inline block, the same
// way cohort_panel.js already calls inline helpers like logHPC().

function loadHpcSettingsToForm(hpc) {
    const presetSel = document.getElementById('hpc_preset');
    // Legacy projects saved before the preset dropdown existed have no
    // hpc.preset -- default them to 'auto' so they immediately reflect
    // the currently configured container instead of stale saved values.
    if (presetSel) presetSel.value = hpc.preset || 'auto';

    // Load raw saved partition/time/mem/cpus/gres first so 'custom'
    // preset (a no-op on these fields) preserves them; applyHpcPreset
    // then overwrites them for any non-custom preset as intended.
    document.getElementById('hpc_partition').value = hpc.partition || '';
    document.getElementById('hpc_time').value = hpc.time || '';
    document.getElementById('hpc_mem').value = hpc.mem || '';
    document.getElementById('hpc_cpus').value = hpc.cpus || 1;
    document.getElementById('hpc_sbatch_gres').value = hpc.sbatch_gres || '';
    applyHpcPreset({ silent: true });

    document.getElementById('hpc_job_name').value = hpc.job_name || '';
    document.getElementById('hpc_output_pattern').value = hpc.output_pattern || '';
    document.getElementById('hpc_error_pattern').value = hpc.error_pattern || '';
    document.getElementById('hpc_notify_email').value = hpc.notify_email || '';
    document.getElementById('hpc_modules').value = (hpc.modules || []).join('\n');
    document.getElementById('hpc_environment').value = JSON.stringify(hpc.environment || {}, null, 2);
    document.getElementById('hpc_monitor_jobs').checked = hpc.monitor_jobs !== false;
    document.getElementById('cohort_max_concurrent').value = hpc.max_concurrent || 50;
    // batch_size can be legitimately 0 (batching disabled) -- only fall
    // back to the 10 default when it's actually missing (null/undefined),
    // not when it's a falsy-but-meaningful 0.
    document.getElementById('cohort_batch_size').value =
        (hpc.batch_size === null || hpc.batch_size === undefined) ? 10 : hpc.batch_size;
    updateCohortDatasetDisplay();
    validateHpcEmailField();
    scheduleHpcPreflightRefresh();
}

function getHpcSettingsFromForm(options = {}) {
    const silent = !!options.silent;
    const modules = document.getElementById('hpc_modules').value
        .split('\n')
        .map(m => m.trim())
        .filter(m => m);

    const partition = (document.getElementById('hpc_partition').value || '').trim();
    const timeVal = (document.getElementById('hpc_time').value || '').trim();
    const memVal = (document.getElementById('hpc_mem').value || '').trim();
    const cpusRaw = (document.getElementById('hpc_cpus').value || '').trim();
    const cpus = parseInt(cpusRaw, 10);

    const envResult = _parseHpcEnvironmentInput(document.getElementById('hpc_environment').value || '');
    if (!envResult.ok) {
        if (!silent) logHPC(envResult.error, true);
        return null;
    }

    if (!partition) {
        if (!silent) logHPC('Partition is required.', true);
        return null;
    }
    if (!timeVal || !_validateHpcTimeFormat(timeVal)) {
        if (!silent) logHPC('Time must be HH:MM:SS or D-HH:MM:SS.', true);
        return null;
    }
    if (!memVal || !_validateHpcMemFormat(memVal)) {
        if (!silent) logHPC('Memory format is invalid. Example: 32G.', true);
        return null;
    }
    if (!Number.isInteger(cpus) || cpus < 1) {
        if (!silent) logHPC('CPUs must be an integer >= 1.', true);
        return null;
    }

    const maxConcurrentRaw = (document.getElementById('cohort_max_concurrent').value || '').trim();
    const maxConcurrent = parseInt(maxConcurrentRaw, 10);
    if (!Number.isInteger(maxConcurrent) || maxConcurrent < 1) {
        if (!silent) logHPC('Max concurrent jobs must be an integer >= 1.', true);
        return null;
    }

    const batchSizeRaw = (document.getElementById('cohort_batch_size').value || '').trim();
    const batchSize = parseInt(batchSizeRaw, 10);
    if (!Number.isInteger(batchSize) || batchSize < 0) {
        if (!silent) logHPC('Batch size must be an integer >= 0 (0 disables batching).', true);
        return null;
    }

    const notifyEmail = (document.getElementById('hpc_notify_email').value || '').trim();
    if (notifyEmail && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(notifyEmail)) {
        if (!silent) logHPC('Notify email is not a valid email address.', true);
        return null;
    }

    return {
        preset: document.getElementById('hpc_preset').value || 'auto',
        partition: partition,
        time: timeVal,
        mem: memVal,
        cpus: cpus,
        sbatch_gres: (document.getElementById('hpc_sbatch_gres').value || '').trim(),
        job_name: (document.getElementById('hpc_job_name').value || '').trim(),
        output_pattern: (document.getElementById('hpc_output_pattern').value || '').trim(),
        error_pattern: (document.getElementById('hpc_error_pattern').value || '').trim(),
        notify_email: notifyEmail,
        modules: modules,
        environment: envResult.environment,
        monitor_jobs: document.getElementById('hpc_monitor_jobs').checked,
        max_concurrent: maxConcurrent,
        batch_size: batchSize
    };
}
