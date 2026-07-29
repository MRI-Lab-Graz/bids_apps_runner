// Reads the Execution Parameters form (dynamic app options, mounts,
// container, HPC-adjacent common fields, and the QSIRecon/FreeSurfer/
// FastSurfer/subregion-segmentation bespoke fields) into {common, app} for
// the currently active pipeline, and syncs the result into
// currentProjectPipelines[currentPipelineId] (+ currentProjectConfig, via
// syncCurrentProjectConfigWithPipelines()) so ANY save path persists the
// same, up-to-date pipeline state -- not just the main Save button.
//
// Extracted after a real incident: "Save HPC settings" cloned
// currentProjectConfig directly, with no equivalent of this sync step, so a
// freshly-created pipeline's output_folder/pipeline_app_name/container
// could still be the stale/default values (from before the pipeline was
// ever fully saved) even though hpc.* itself saved fine. Cohort Setup then
// read that stale snapshot and registered/cloned the WRONG dataset
// (operated on an old FreeSurfer pipeline's output while the user believed
// they'd just set up QSIPrep).
//
// Deliberately does NOT abort on missing required fields -- returns them
// instead (missingRequired, missingReconSpec) so each caller decides: the
// main Save button blocks the whole save on them (matching prior
// behavior), but an HPC-only save shouldn't be blocked by an unrelated,
// still-incomplete app option -- and either way, syncing the in-memory
// state to match what's currently on screen is strictly more correct than
// leaving it stale, even mid-edit.
//
// Same shared-state contract as project_loader.js/cohort_panel.js: reads
// and writes currentProjectPipelines/currentPipelineId/currentContainerPath
// (var-declared in the inline script below) rather than taking them as
// parameters, since every call site already operates on that same global
// project-editing state.
function syncActivePipelineFromForm() {
    const opts = [];
    const missingRequired = [];

    document.querySelectorAll('.dynamic-opt[data-required="true"]').forEach(el => {
        if (!el.value || !el.value.trim()) {
            missingRequired.push(el.dataset.flag);
        }
    });

    document.querySelectorAll('.dynamic-opt-bool[data-required="true"]').forEach(el => {
        const val = (el.value || '').trim();
        if (!val) {
            missingRequired.push(el.dataset.flag);
        }
    });

    document.querySelectorAll('.dynamic-opt-multi-wrap[data-required="true"]').forEach(el => {
        const flag = el.dataset.flag;
        const checked = document.querySelectorAll(`.dynamic-opt-multi[data-flag="${flag}"]:checked`).length;
        if (checked === 0) {
            missingRequired.push(flag);
        }
    });

    const missingReconSpec = isQsireconContainer() && !document.getElementById('qsirecon_recon_spec').value;

    // Handle standard inputs
    document.querySelectorAll('.dynamic-opt').forEach(el => {
        if (el.type === 'checkbox') {
            if (el.checked) opts.push(el.dataset.flag);
        } else if (el.value.trim()) {
            opts.push(el.dataset.flag);
            if (el.tagName === 'TEXTAREA') {
                // Split on whitespace/newlines AND commas -- e.g. "1,2" (no
                // space) is a very natural way to type a list despite the
                // "one per line" placeholder, and no legitimate single
                // value for these multi-value flags (session labels,
                // participant labels, etc.) would ever legitimately
                // contain a literal comma.
                const vals = el.value.trim().split(/[\s,]+/);
                vals.forEach(v => { if (v) opts.push(v); });
            } else {
                opts.push(el.value.trim());
            }
        }
    });

    // Handle Boolean selection (Enabled/Disabled)
    document.querySelectorAll('.dynamic-opt-bool').forEach(el => {
        const flag = el.dataset.flag;
        const isNegated = el.dataset.negated === 'true';
        const val = el.value; // 'enabled' or 'disabled'

        if (isNegated) {
            // For negated flag, "Disabled" means we add the skip/no flag
            if (val === 'disabled') opts.push(flag);
        } else {
            // For standard flag, "Enabled" means we add it
            if (val === 'enabled') opts.push(flag);
        }
    });

    const multi = {};
    document.querySelectorAll('.dynamic-opt-multi').forEach(el => {
        if (el.checked) { const f = el.dataset.flag; if (!multi[f]) multi[f] = []; multi[f].push(el.value); }
    });
    for (const f in multi) { opts.push(f); multi[f].forEach(v => opts.push(v)); }

    // Add custom arguments from the textarea
    const customArgsVal = document.getElementById('custom_args').value.trim();
    if (customArgsVal) {
        const args = customArgsVal.split(/[\s\n]+/);
        args.forEach(a => { if (a) opts.push(a); });
    }

    // QSIRecon: the dropdown is the authoritative source for --recon-spec.
    // Strip any --recon-spec already collected from dynamic options or
    // custom_args, then inject the dropdown value so it always wins.
    if (isQsireconContainer()) {
        const reconSpec = document.getElementById('qsirecon_recon_spec').value;
        for (let i = opts.length - 1; i >= 0; i--) {
            if (opts[i] === '--recon-spec') {
                opts.splice(i, 2); // remove flag and its value
            }
        }
        if (reconSpec) {
            opts.push('--recon-spec', reconSpec);
        }

        // --fs-subjects-dir: remove any existing, then inject from the
        // dedicated field. The actual bind mount is added to mounts below.
        const fsDirHost = document.getElementById('qsirecon_fs_subjects_dir').value.trim();
        for (let i = opts.length - 1; i >= 0; i--) {
            if (opts[i] === '--fs-subjects-dir') {
                opts.splice(i, 2);
            }
        }
        if (fsDirHost) {
            opts.push('--fs-subjects-dir', '/fssubjects');
        }

        // --atlases: strip existing (may span multiple non-flag values), then inject checked atlases.
        for (let i = opts.length - 1; i >= 0; i--) {
            if (opts[i] === '--atlases') {
                let j = i + 1;
                while (j < opts.length && !opts[j].startsWith('-')) j++;
                opts.splice(i, j - i);
            }
        }
        const spec = document.getElementById('qsirecon_recon_spec').value;
        if (QSIRECON_CONNECTIVITY_WORKFLOWS.has(spec)) {
            const checkedAtlases = [...document.querySelectorAll('.qsirecon-atlas:checked')].map(cb => cb.value);
            if (checkedAtlases.length > 0) {
                opts.push('--atlases', ...checkedAtlases);
            }
        }
    }

    const mounts = [];
    document.getElementById('custom_mounts').value.split('\n').forEach(line => {
        const parts = line.trim().split(':');
        if (parts.length >= 2) {
            mounts.push({ source: parts[0].trim(), target: parts.slice(1).join(':').trim() });
        }
    });

    // QSIRecon: add bind mount for --fs-subjects-dir if provided
    if (isQsireconContainer()) {
        const fsDirHost = document.getElementById('qsirecon_fs_subjects_dir').value.trim();
        if (fsDirHost) {
            const idx = mounts.findIndex(m => m.target === '/fssubjects');
            if (idx !== -1) mounts.splice(idx, 1);
            mounts.push({ source: fsDirHost, target: '/fssubjects:ro' });
        }
    }

    const engine = document.getElementById('container_engine').value;
    let containerPath;
    if (engine === 'docker') {
        const repo = document.getElementById('container_docker_repo').value.trim();
        const tag = document.getElementById('container_docker_tag').value;
        containerPath = `${repo}:${tag}`;
    } else {
        containerPath = document.getElementById('container_folder').value + '/' + document.getElementById('container_select').value;
    }

    // Container locking logic:
    // - If container path changed: unlock to allow options reload on next load
    // - If same container: lock to preserve user customizations
    let containerLocked = true;
    if (containerPath !== currentContainerPath) {
        containerLocked = false;
        currentContainerPath = containerPath;
    }

    applyVersionedOutputPaths({ source: 'save' });
    const resolvedPipelineAppName = inferCurrentPipelineAppName();

    const activeCommon = {
        bids_folder: document.getElementById('bids_folder').value,
        output_folder: document.getElementById('output_folder').value,
        tmp_folder: document.getElementById('tmp_folder').value,
        templateflow_dir: document.getElementById('templateflow_dir').value,
        fs_license_file: document.getElementById('fs_license_file').value,
        pipeline_output_root: (document.getElementById('pipeline_output_root').value || '').trim(),
        pipeline_app_name: resolvedPipelineAppName,
        pipeline_version: (document.getElementById('pipeline_version').value || '').trim(),
        pipeline_auto_versioning: !!document.getElementById('pipeline_auto_versioning').checked,
        notify_email: document.getElementById('notify_email').value.trim(),
        container_engine: engine,
        container: containerPath,
        container_locked: containerLocked,
        jobs: parseInt(document.getElementById('jobs').value) || 1
    };

    // Comma-separated, e.g. "1,2" or "ses-1,ses-2" -- normalized to full
    // "ses-X" labels here so scripts/check_app_output.py's --sessions flag
    // (and get_sessions() filtering) always receives canonical session
    // dirnames to match against, regardless of how the user typed it.
    const expectedSessionsEl = document.getElementById('expected_sessions');
    const expectedSessions = (expectedSessionsEl ? expectedSessionsEl.value : '')
        .split(',')
        .map(s => s.trim())
        .filter(Boolean)
        .map(s => (s.startsWith('ses-') ? s : `ses-${s}`));

    const gpuEnabledEl = document.getElementById('gpu_enabled');
    const activeApp = {
        analysis_level: document.getElementById('analysis_level').value,
        options: opts,
        mounts: mounts,
        expected_sessions: expectedSessions,
        ...(gpuEnabledEl && !gpuEnabledEl.disabled && !gpuEnabledEl.checked ? { disable_gpu: true } : {})
    };

    const existingPipelineApp = (
        lastProjectId && currentPipelineId && currentProjectPipelines[currentPipelineId]
    )
        ? (currentProjectPipelines[currentPipelineId].app || {})
        : {};
    if (
        existingPipelineApp.option_help_cache &&
        _isOptionHelpCacheUsable(existingPipelineApp.option_help_cache, containerPath, engine)
    ) {
        activeApp.option_help_cache = cloneJson(existingPipelineApp.option_help_cache);
    }

    if (resolvedPipelineAppName === 'fastsurfer') {
        const fastsurferLongitudinalEl = document.getElementById('fastsurfer_longitudinal');
        activeApp.execution_adapter = (fastsurferLongitudinalEl && fastsurferLongitudinalEl.checked)
            ? 'fastsurfer-bids'
            : 'fastsurfer-cross';
    } else if (resolvedPipelineAppName === 'freesurfer') {
        const freesurferBidsLongitudinalEl = document.getElementById('freesurfer_bids_longitudinal');
        if (freesurferBidsLongitudinalEl && freesurferBidsLongitudinalEl.checked) {
            activeApp.execution_adapter = 'freesurfer-bids';
        }
    }
    if (typeof collectSubregionSegmentationConfig === 'function') {
        const subregionCfg = collectSubregionSegmentationConfig();
        if (subregionCfg) activeApp.subregion_segmentation = subregionCfg;
    }

    if (lastProjectId) {
        const pipelineState = ensureProjectPipelineState(currentProjectConfig || {});
        currentProjectPipelines = pipelineState.pipelines;

        if (!currentPipelineId || !currentProjectPipelines[currentPipelineId]) {
            currentPipelineId = pipelineState.activePipeline || 'default';
        }

        const existingEntry = normalizePipelineEntry(
            currentProjectPipelines[currentPipelineId] || {},
            currentPipelineId
        );
        currentProjectPipelines[currentPipelineId] = {
            ...existingEntry,
            common: cloneJson(activeCommon) || {},
            app: cloneJson(activeApp) || {},
        };
        renderPipelinePresetSelector();
        // The missing link that caused the incident above: without this,
        // currentProjectConfig (what saveHPCSettings() and anything else
        // that clones it actually persists) only ever reflects the DOM
        // when the main Save button's own handler happened to run this
        // same construction inline -- any other save path saw whatever
        // currentProjectConfig last was, however stale.
        syncCurrentProjectConfigWithPipelines();
    }

    return {
        common: activeCommon,
        app: activeApp,
        missingRequired: Array.from(new Set(missingRequired)),
        missingReconSpec,
    };
}
