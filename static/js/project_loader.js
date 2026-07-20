/**
 * Project & pipeline loading, and container/app-options fetching.
 *
 * Extracted from the ~8800-line inline <script> in templates/index.html --
 * first slice pulled out of that monolith, covering one coherent subsystem:
 * loading a project from disk into the Runner form (applyProjectData /
 * restoreProjectLoadDetails), switching between a project's pipeline presets
 * (applyPipelineEntryToRunnerForm and friends), and everything involved in
 * resolving "which container is selected" and fetching/caching/rendering its
 * --help-derived options (scanContainers, fetchAppOptions,
 * updateFetchHelpBtnVisibility, etc).
 *
 * This is a plain classic <script src="...">, not an ES module -- loaded
 * before the inline <script> (see templates/index.html), sharing the same
 * global (window) scope on purpose, the same way static/js/readiness_panel.js
 * already does. Function declarations are global regardless of which script
 * tag defines them, so the boundary here is cosmetic for those; the state
 * these functions read/write (currentContainerPath, currentOptionHelpCache,
 * currentProjectPipelines, lastProjectId, etc.) is declared with `var` rather
 * than `let`/`const` in the inline script specifically so it's visible here
 * too -- `let`/`const` at a script's top level do NOT become window
 * properties and would not cross the file boundary, unlike `var`.
 */

function cloneJson(value) {
    if (value === undefined) return undefined;
    return JSON.parse(JSON.stringify(value));
}

function sanitizePipelineId(value) {
    const raw = String(value || '')
        .trim()
        .toLowerCase();
    const normalized = raw
        .replace(/[^a-z0-9_]+/g, '_')
        .replace(/_+/g, '_')
        .replace(/^_+|_+$/g, '');
    return normalized || 'default';
}

function normalizePipelineEntry(rawEntry, fallbackName = 'Pipeline') {
    const entry = rawEntry && typeof rawEntry === 'object' ? rawEntry : {};

    let app = {};
    if (entry.app && typeof entry.app === 'object') {
        app = cloneJson(entry.app) || {};
    } else if (
        Object.prototype.hasOwnProperty.call(entry, 'analysis_level') ||
        Object.prototype.hasOwnProperty.call(entry, 'options') ||
        Object.prototype.hasOwnProperty.call(entry, 'mounts')
    ) {
        app = cloneJson(entry) || {};
    }

    if (!Array.isArray(app.options)) app.options = [];
    if (!Array.isArray(app.mounts)) app.mounts = [];
    if (!app.analysis_level) app.analysis_level = 'participant';

    const common =
        entry.common && typeof entry.common === 'object' ? cloneJson(entry.common) || {} : {};

    return {
        name: String(entry.name || fallbackName).trim() || fallbackName,
        description: String(entry.description || '').trim(),
        common,
        app,
    };
}

function buildFreshPipelinePreset(presetName) {
    const source = normalizePipelineEntry(
        getCurrentPipelineEntry() || {},
        presetName || 'Pipeline',
    );
    const sourceCommon = source.common || {};
    const inferredApp = sanitizePipelineId(presetName || '').replace(/_\d+$/g, '');

    return {
        name: String(presetName || 'Pipeline').trim() || 'Pipeline',
        description: '',
        common: {
            bids_folder: sourceCommon.bids_folder || '',
            output_folder: sourceCommon.output_folder || '',
            tmp_folder: sourceCommon.tmp_folder || '',
            templateflow_dir: sourceCommon.templateflow_dir || '',
            fs_license_file: sourceCommon.fs_license_file || '',
            pipeline_output_root: sourceCommon.pipeline_output_root || '',
            pipeline_app_name: sourceCommon.pipeline_app_name || inferredApp || '',
            pipeline_version: '',
            pipeline_auto_versioning: sourceCommon.pipeline_auto_versioning === true,
            notify_email: sourceCommon.notify_email || '',
            container_engine: sourceCommon.container_engine || 'apptainer',
            container: '',
            container_locked: false,
            jobs: sourceCommon.jobs || 1,
        },
        app: {
            analysis_level: source.app?.analysis_level || 'participant',
            options: [],
            mounts: [],
        },
    };
}

function ensureProjectPipelineState(cfg) {
    const safeCfg = cfg && typeof cfg === 'object' ? cfg : {};
    const normalizedPipelines = {};

    const rawPipelines =
        safeCfg.pipelines && typeof safeCfg.pipelines === 'object' ? safeCfg.pipelines : {};

    Object.entries(rawPipelines).forEach(([rawId, rawEntry]) => {
        const pipelineId = sanitizePipelineId(rawId);
        normalizedPipelines[pipelineId] = normalizePipelineEntry(rawEntry, rawId || pipelineId);
    });

    if (Object.keys(normalizedPipelines).length === 0) {
        normalizedPipelines.default = normalizePipelineEntry(
            {
                name: 'Default Pipeline',
                common: safeCfg.common && typeof safeCfg.common === 'object' ? safeCfg.common : {},
                app:
                    safeCfg.app && typeof safeCfg.app === 'object'
                        ? safeCfg.app
                        : { analysis_level: 'participant', options: [], mounts: [] },
            },
            'Default Pipeline',
        );
    }

    let activePipeline = sanitizePipelineId(safeCfg.active_pipeline || 'default');
    if (!normalizedPipelines[activePipeline]) {
        activePipeline = Object.keys(normalizedPipelines)[0];
    }

    return {
        pipelines: normalizedPipelines,
        activePipeline,
    };
}

function getCurrentPipelineEntry() {
    if (!currentPipelineId) return null;
    return currentProjectPipelines[currentPipelineId] || null;
}

function getCurrentPipelineName() {
    const entry = getCurrentPipelineEntry();
    return entry && entry.name ? entry.name : currentPipelineId;
}

function renderPipelinePresetSelector() {
    const section = document.getElementById('runnerPipelineSection');
    const selectEl = document.getElementById('active_pipeline_select');
    const deleteBtn = document.getElementById('deletePipelinePresetBtn');
    if (!section || !selectEl) return;

    const pipelineIds = Object.keys(currentProjectPipelines || {});
    if (!lastProjectId || pipelineIds.length === 0) {
        section.style.display = 'none';
        selectEl.innerHTML = '';
        return;
    }

    section.style.display = 'block';
    selectEl.innerHTML = '';

    pipelineIds.forEach((pipelineId) => {
        const entry = currentProjectPipelines[pipelineId] || {};
        const label = entry.name || pipelineId;
        selectEl.add(new Option(label, pipelineId));
    });

    if (currentPipelineId && currentProjectPipelines[currentPipelineId]) {
        selectEl.value = currentPipelineId;
    } else if (pipelineIds.length > 0) {
        currentPipelineId = pipelineIds[0];
        selectEl.value = currentPipelineId;
    }

    if (deleteBtn) {
        deleteBtn.disabled = pipelineIds.length <= 1;
        deleteBtn.title =
            pipelineIds.length <= 1
                ? 'At least one pipeline preset is required.'
                : 'Delete the active pipeline preset.';
    }
    const renameBtn = document.getElementById('renamePipelinePresetBtn');
    if (renameBtn) {
        renameBtn.disabled = pipelineIds.length === 0;
        renameBtn.title = 'Rename the active pipeline preset.';
    }
}

function syncCurrentProjectConfigWithPipelines() {
    if (!currentProjectConfig || typeof currentProjectConfig !== 'object') return;
    if (!currentPipelineId || !currentProjectPipelines[currentPipelineId]) return;

    const selected = currentProjectPipelines[currentPipelineId];
    currentProjectConfig.pipelines = cloneJson(currentProjectPipelines) || {};
    currentProjectConfig.active_pipeline = currentPipelineId;
    currentProjectConfig.common = cloneJson(selected.common) || {};
    currentProjectConfig.app = cloneJson(selected.app) || {};
}

function setNavbarProjectName(name) {
    const container = document.getElementById('navbarProject');
    const nameEl = document.getElementById('navbarProjectName');
    if (!container || !nameEl) return;

    const cleaned = (name || '').trim();
    if (cleaned) {
        nameEl.textContent = cleaned;
        container.classList.remove('is-empty');
    } else {
        nameEl.textContent = 'No project loaded';
        container.classList.add('is-empty');
    }
}

async function applyPipelineEntryToRunnerForm(entry, options = {}) {
    const markDirty = !!options.markDirty;
    const loadToken = ++activeProjectLoadToken;
    const safeEntry = normalizePipelineEntry(
        entry || {},
        getCurrentPipelineName() || currentPipelineId || 'Pipeline',
    );
    const common = safeEntry.common || {};
    const app = safeEntry.app || {};

    suppressProjectDirtyTracking = true;
    try {
        document.getElementById('bids_folder').value = common.bids_folder || '';
        updateCohortDatasetDisplay(common.bids_folder || '');
        switchBidsSourceMode(inferBidsSourceMode(common.bids_folder));
        document.getElementById('output_folder').value = common.output_folder || '';
        document.getElementById('tmp_folder').value = common.tmp_folder || '';
        document.getElementById('templateflow_dir').value = common.templateflow_dir || '';
        document.getElementById('fs_license_file').value = common.fs_license_file || '';
        const inferredRoot = _derivePipelineRootFromOutput(
            common.output_folder || '',
            common.pipeline_app_name || '',
            common.pipeline_version || '',
        );
        document.getElementById('pipeline_output_root').value =
            common.pipeline_output_root || inferredRoot || '';
        document.getElementById('pipeline_app_name').value = common.pipeline_app_name || '';
        document.getElementById('pipeline_version').value = common.pipeline_version || '';
        document.getElementById('pipeline_auto_versioning').checked =
            common.pipeline_auto_versioning === true;
        document.getElementById('notify_email').value = common.notify_email || '';
        document.getElementById('jobs').value = common.jobs || 1;
        document.getElementById('analysis_level').value = app.analysis_level || 'participant';
        syncRunnerSubjectFilterWithAnalysisLevel();
        const gpuChk = document.getElementById('gpu_enabled');
        if (gpuChk && !gpuChk.disabled) gpuChk.checked = !app.disable_gpu;
        const fastsurferLongitudinalChk = document.getElementById('fastsurfer_longitudinal');
        if (fastsurferLongitudinalChk) {
            // Either signal counts: app.execution_adapter is the
            // authoritative field actually used at runtime, but
            // hpc.preset (set via the HPC tab's Compute Preset
            // dropdown) is an equally explicit user choice that must
            // not get silently lost if execution_adapter drifted out
            // of sync with it (e.g. a stale save from before the
            // preset was picked) -- restoring from execution_adapter
            // alone would un-check this and then re-save the stale
            // value right back on the next Save.
            const presetSaysLongitudinal =
                currentProjectConfig &&
                currentProjectConfig.hpc &&
                currentProjectConfig.hpc.preset === 'fastsurfer_bids';
            fastsurferLongitudinalChk.checked =
                app.execution_adapter === 'fastsurfer-bids' || !!presetSaysLongitudinal;
        }

        const engine = common.container_engine || 'apptainer';
        document.getElementById('container_engine').value = engine;

        if (engine === 'docker') {
            const containerImage = common.container || '';
            const parts = containerImage.split(':');
            const savedTag = parts[1] || 'latest';
            document.getElementById('container_docker_repo').value = parts[0] || '';
            const tagSelectA = document.getElementById('container_docker_tag');
            if (!Array.from(tagSelectA.options).some((o) => o.value === savedTag)) {
                tagSelectA.innerHTML = '';
                tagSelectA.add(new Option(savedTag, savedTag));
            }
            tagSelectA.value = savedTag;
        } else {
            const containerPath = common.container || '';
            if (containerPath) {
                const folder = containerPath.substring(0, containerPath.lastIndexOf('/'));
                document.getElementById('container_folder').value = folder;
            } else {
                document.getElementById('container_folder').value = '';
            }
        }

        // See the matching comment in applyProjectData: this must be set
        // before applyMachineSettingsToRunnerForm() runs below, or a
        // pipeline with its own saved container gets mistaken for "no
        // container configured yet" while container_select is still
        // being populated for the newly-switched pipeline.
        currentContainerPath = common.container || '';
        containerLocked = common.container_locked || false;

        toggleEngineFields();
        applyMachineSettingsToRunnerForm({ silent: true, fillOnlyEmpty: true });
        applyVersionedOutputPaths({ source: 'pipeline-switch' });

        if (!common.container) {
            document.getElementById('custom_args').value = '';
            document.getElementById('custom_mounts').value = '';
            document.getElementById('qsirecon_fs_subjects_dir').value = '';
            document.getElementById('dynamicOptionsSection').style.display = 'none';
            document.getElementById('optionsContainer').innerHTML = '';
        }
    } finally {
        suppressProjectDirtyTracking = false;
    }

    await restoreProjectLoadDetails({ app }, common, loadToken);
    syncCurrentProjectConfigWithPipelines();
    updateLoadedProjectSummary();
    scheduleRunnerPreflightRefresh();
    scheduleCheckPreflightRefresh();
    scheduleBuildPreflightRefresh();

    if (markDirty) {
        setProjectDirty(true);
    }
}

async function switchActivePipelineById(pipelineId, options = {}) {
    const markDirty = !!options.markDirty;
    const allowUnsavedDiscard = options.allowUnsavedDiscard !== false;
    const nextId = sanitizePipelineId(pipelineId);
    const selectEl = document.getElementById('active_pipeline_select');

    if (!nextId || !currentProjectPipelines[nextId]) {
        if (selectEl && currentPipelineId) {
            selectEl.value = currentPipelineId;
        }
        return;
    }

    if (nextId === currentPipelineId) {
        if (selectEl) selectEl.value = currentPipelineId;
        return;
    }

    if (
        allowUnsavedDiscard &&
        hasUnsavedProjectChanges() &&
        !confirmDiscardUnsavedChanges('switch pipeline presets')
    ) {
        if (selectEl && currentPipelineId) {
            selectEl.value = currentPipelineId;
        }
        return;
    }

    currentPipelineId = nextId;
    if (selectEl) {
        selectEl.value = currentPipelineId;
    }
    await applyPipelineEntryToRunnerForm(currentProjectPipelines[currentPipelineId], { markDirty });
}

async function switchActivePipelineFromRunner() {
    const selectEl = document.getElementById('active_pipeline_select');
    if (!selectEl) return;
    await switchActivePipelineById(selectEl.value, { markDirty: true, allowUnsavedDiscard: true });
}

async function createPipelinePreset() {
    if (!lastProjectId) {
        showStatus('Load a project before creating pipeline presets.', true);
        return;
    }

    if (
        hasUnsavedProjectChanges() &&
        !confirmDiscardUnsavedChanges('create a new pipeline preset')
    ) {
        return;
    }

    const suggested = `Pipeline ${Object.keys(currentProjectPipelines || {}).length + 1}`;
    const enteredName = prompt('Name for the new pipeline preset:', suggested);
    if (enteredName === null) return;

    const cleanName = String(enteredName || '').trim() || suggested;
    let pipelineId = sanitizePipelineId(cleanName);
    const existingIds = new Set(Object.keys(currentProjectPipelines || {}));
    if (existingIds.has(pipelineId)) {
        let counter = 2;
        while (existingIds.has(`${pipelineId}_${counter}`)) {
            counter += 1;
        }
        pipelineId = `${pipelineId}_${counter}`;
    }

    currentProjectPipelines[pipelineId] = buildFreshPipelinePreset(cleanName);
    renderPipelinePresetSelector();
    await switchActivePipelineById(pipelineId, { markDirty: true, allowUnsavedDiscard: false });
    showStatus(`Created pipeline preset: ${cleanName}`);
}

function renameActivePipelinePreset() {
    if (!lastProjectId) {
        showStatus('Load a project before renaming pipeline presets.', true);
        return;
    }
    const entry = currentProjectPipelines[currentPipelineId];
    if (!entry) return;

    const currentName = entry.name || currentPipelineId;
    const newName = prompt('Rename pipeline preset:', currentName);
    if (newName === null) return;

    const cleanName = String(newName || '').trim();
    if (!cleanName || cleanName === currentName) return;

    entry.name = cleanName;
    renderPipelinePresetSelector();
    syncCurrentProjectConfigWithPipelines();
    setProjectDirty(true);
    showStatus(`Pipeline preset renamed to: ${cleanName}`);
}

async function deleteActivePipelinePreset() {
    if (!lastProjectId) {
        showStatus('Load a project before deleting pipeline presets.', true);
        return;
    }

    const pipelineIds = Object.keys(currentProjectPipelines || {});
    if (pipelineIds.length <= 1) {
        showStatus('At least one pipeline preset must remain.', true);
        return;
    }

    const currentName = getCurrentPipelineName() || currentPipelineId;
    if (!confirm(`Delete pipeline preset "${currentName}"?`)) {
        return;
    }

    delete currentProjectPipelines[currentPipelineId];
    const fallbackId = Object.keys(currentProjectPipelines)[0];
    renderPipelinePresetSelector();
    await switchActivePipelineById(fallbackId, { markDirty: true, allowUnsavedDiscard: false });
    showStatus(`Deleted pipeline preset: ${currentName}`);
}

function applyMachineSettingsToRunnerForm(options = {}) {
    const silent = !!options.silent;
    const fillOnlyEmpty = !!options.fillOnlyEmpty;
    const effective = globalMachineSettings && globalMachineSettings.effective;
    if (!effective) {
        if (!silent) showStatus('Load machine settings first.', true);
        return false;
    }

    const resolvedEngine =
        effective.resolved_container_engine || effective.preferred_container_engine || 'apptainer';

    const engineEl = document.getElementById('container_engine');
    const jobsEl = document.getElementById('jobs');
    const tmpFolderEl = document.getElementById('tmp_folder');
    const apptainerFolderEl = document.getElementById('container_folder');
    const apptainerSelectEl = document.getElementById('container_select');
    const dockerRepoEl = document.getElementById('container_docker_repo');
    const dockerTagEl = document.getElementById('container_docker_tag');
    const buildOutputDirEl = document.getElementById('build_output_dir');
    const buildTmpDirEl = document.getElementById('build_tmp_dir');
    const buildDockerRepoEl = document.getElementById('build_docker_repo');
    const buildDockerTagEl = document.getElementById('build_docker_tag');

    const defaultApptainerContainer = (effective.default_apptainer_container || '').trim();
    let defaultApptainerFolder = (effective.default_apptainer_folder || '').trim();
    let defaultApptainerImage = '';
    if (defaultApptainerContainer && defaultApptainerContainer.includes('/')) {
        const lastSlash = defaultApptainerContainer.lastIndexOf('/');
        if (!defaultApptainerFolder) {
            defaultApptainerFolder = defaultApptainerContainer.slice(0, lastSlash);
        }
        defaultApptainerImage = defaultApptainerContainer.slice(lastSlash + 1);
    }

    const defaultTmpFolder = (effective.default_tmp_folder || '').trim();
    const defaultTemplateflowDir = (effective.default_templateflow_dir || '').trim();

    if (!fillOnlyEmpty || !(jobsEl.value || '').trim()) {
        jobsEl.value = String(effective.default_jobs || 1);
    }

    if (!fillOnlyEmpty || !(tmpFolderEl.value || '').trim()) {
        tmpFolderEl.value = defaultTmpFolder;
    }

    const templateflowEl = document.getElementById('templateflow_dir');
    if (templateflowEl && defaultTemplateflowDir) {
        templateflowEl.value = defaultTemplateflowDir;
    }

    // currentContainerPath covers the case where a project/pipeline load has
    // already recorded its saved container but container_select hasn't been
    // repopulated with it yet (that scan happens later, asynchronously) --
    // without this, a project's own container looks "unconfigured" for a
    // moment and gets clobbered by the machine's default image below.
    const hasConfiguredContainer = !!getConfiguredContainerValue() || !!currentContainerPath;
    if (!fillOnlyEmpty || (!lastProjectId && !hasConfiguredContainer)) {
        engineEl.value = resolvedEngine;
    }

    if (resolvedEngine === 'apptainer') {
        if (!fillOnlyEmpty || !(apptainerFolderEl.value || '').trim()) {
            apptainerFolderEl.value = defaultApptainerFolder;
        }

        if (defaultApptainerImage && (!fillOnlyEmpty || !hasConfiguredContainer)) {
            const hasOption = Array.from(apptainerSelectEl.options).some(
                (opt) => opt.value === defaultApptainerImage,
            );
            if (!hasOption) {
                apptainerSelectEl.add(new Option(defaultApptainerImage, defaultApptainerImage));
            }
            apptainerSelectEl.value = defaultApptainerImage;
            updateFetchHelpBtnVisibility();
        }
    } else if (resolvedEngine === 'docker') {
        if (!fillOnlyEmpty || !(dockerRepoEl.value || '').trim()) {
            dockerRepoEl.value = effective.default_docker_repo || '';
        }
        if (!fillOnlyEmpty || !(dockerTagEl.value || '').trim()) {
            const defaultTag = effective.default_docker_tag || 'latest';
            const hasTagOption = Array.from(dockerTagEl.options).some(
                (opt) => opt.value === defaultTag,
            );
            if (!hasTagOption) {
                dockerTagEl.add(new Option(defaultTag, defaultTag));
            }
            dockerTagEl.value = defaultTag;
        }
    }

    if (buildOutputDirEl) {
        const buildDefaultOutput = defaultApptainerFolder;
        if (!fillOnlyEmpty || !(buildOutputDirEl.value || '').trim()) {
            buildOutputDirEl.value = buildDefaultOutput;
        }
    }

    if (buildTmpDirEl) {
        if (!fillOnlyEmpty || !(buildTmpDirEl.value || '').trim()) {
            buildTmpDirEl.value = defaultTmpFolder;
        }
    }

    if (buildDockerRepoEl) {
        if (!fillOnlyEmpty || !(buildDockerRepoEl.value || '').trim()) {
            buildDockerRepoEl.value = effective.default_docker_repo || '';
        }
    }

    if (buildDockerTagEl) {
        const defaultTag = effective.default_docker_tag || 'latest';
        if (!fillOnlyEmpty || !(buildDockerTagEl.value || '').trim()) {
            const hasTagOption = Array.from(buildDockerTagEl.options).some(
                (opt) => opt.value === defaultTag,
            );
            if (!hasTagOption) {
                buildDockerTagEl.add(new Option(defaultTag, defaultTag));
            }
            buildDockerTagEl.value = defaultTag;
        }
    }

    toggleEngineFields();
    scheduleRunnerPreflightRefresh();
    scheduleBuildPreflightRefresh();

    if (!silent) {
        showStatus(`Applied machine defaults (engine: ${resolvedEngine}).`);
    }
    return true;
}

function getConfiguredContainerValue() {
    const engine = document.getElementById('container_engine').value;
    if (engine === 'docker') {
        const repo = (document.getElementById('container_docker_repo').value || '').trim();
        const tag = (document.getElementById('container_docker_tag').value || '').trim();
        if (!repo || !tag) return '';
        return `${repo}:${tag}`;
    }

    const folder = (document.getElementById('container_folder').value || '').trim();
    const image = (document.getElementById('container_select').value || '').trim();
    if (!folder || !image) return '';
    return `${folder}/${image}`;
}

async function loadRecentProjects() {
    try {
        const resp = await fetch('/get_projects');
        const data = await resp.json();
        const list = document.getElementById('recentProjectsList');
        const meta = document.getElementById('recentProjectsMeta');

        if (!resp.ok) {
            throw new Error(data.error || 'Failed to load recent projects');
        }

        if (!data.projects || data.projects.length === 0) {
            list.innerHTML =
                '<div class="list-group-item text-muted">No projects yet. Create one to get started!</div>';
            if (meta) meta.textContent = 'No projects found.';
            return;
        }

        const totalProjects = Number.isInteger(data.total_projects)
            ? data.total_projects
            : data.projects.length;
        const shownCount = data.projects.length;
        const limit = Number.isInteger(data.limit) ? data.limit : RECENT_PROJECTS_LIMIT;
        if (meta) {
            let metaText = `Showing ${shownCount} of ${totalProjects} projects (latest first).`;
            if (totalProjects > shownCount || shownCount >= limit) {
                metaText += ' Use "Load Project from File" to open older projects.';
            }
            meta.textContent = metaText;
        }

        list.innerHTML = '';
        data.projects.forEach((proj) => {
            const cfg = proj.config && typeof proj.config === 'object' ? proj.config : {};
            const pipelineState = ensureProjectPipelineState(cfg);
            const activePipelineId = pipelineState.activePipeline;
            const activePipeline = pipelineState.pipelines[activePipelineId] || {};
            const common =
                activePipeline.common && typeof activePipeline.common === 'object'
                    ? activePipeline.common
                    : cfg.common && typeof cfg.common === 'object'
                      ? cfg.common
                      : {};
            const app =
                activePipeline.app && typeof activePipeline.app === 'object'
                    ? activePipeline.app
                    : cfg.app && typeof cfg.app === 'object'
                      ? cfg.app
                      : {};
            const requiredMissing = [];
            if (!common.bids_folder) requiredMissing.push('BIDS folder');
            if (!common.output_folder) requiredMissing.push('Output folder');
            if (!common.container) requiredMissing.push('Container');
            if (!app.analysis_level) requiredMissing.push('Analysis level');

            const pipelineName = activePipeline.name || activePipelineId || 'default';

            const readinessHtml =
                requiredMissing.length === 0
                    ? `<span class="badge bg-success">Ready to run (${escapeHtml(pipelineName)})</span>`
                    : `<span class="badge bg-warning text-dark">Missing (${escapeHtml(pipelineName)}): ${escapeHtml(requiredMissing.join(', '))}</span>`;

            const item = document.createElement('div');
            item.className = 'list-group-item';
            if (proj.id === lastProjectId) item.classList.add('active');
            item.innerHTML = `
                        <div class="d-flex justify-content-between align-items-start gap-2">
                            <div>
                                <button type="button" class="btn btn-link p-0 text-decoration-none fw-semibold project-load-btn">${escapeHtml(proj.name || proj.id)}</button>
                                <div class="small text-muted">${escapeHtml(proj.description || 'No description')}</div>
                                <div class="small text-muted">Modified: ${escapeHtml(_formatDateTimeForDisplay(proj.last_modified))}</div>
                                <div class="small mt-1">${readinessHtml}</div>
                            </div>
                            <button type="button" class="btn btn-sm btn-outline-danger project-delete-btn" title="Delete project permanently">
                                <i class="fas fa-trash"></i>
                            </button>
                        </div>
                    `;

            const loadBtn = item.querySelector('.project-load-btn');
            const deleteBtn = item.querySelector('.project-delete-btn');
            if (loadBtn) loadBtn.onclick = () => loadProjectAndSwitch(proj.id);
            if (deleteBtn) {
                deleteBtn.onclick = (event) => {
                    event.stopPropagation();
                    deleteProject(proj.id, proj.name || proj.id);
                };
            }

            list.appendChild(item);
        });
    } catch (e) {
        console.error('Failed to load projects:', e);
        document.getElementById('recentProjectsList').innerHTML =
            '<div class="alert alert-danger">Failed to load projects</div>';
        const meta = document.getElementById('recentProjectsMeta');
        if (meta) meta.textContent = 'Unable to fetch project list.';
    }
}

async function loadProjectFromPath() {
    const projectPath = document.getElementById('loadProjectPath').value.trim();
    if (!projectPath) {
        showStatus('Please select a project.json file', true);
        return;
    }

    if (!confirmDiscardUnsavedChanges('load another project')) {
        return;
    }

    try {
        const resp = await fetch('/load_project_file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: projectPath }),
        });
        const project = await resp.json();

        if (!resp.ok) {
            showStatus(project.error || 'Failed to load project', true);
            return;
        }

        if (!project.id || !project.name || !project.config) {
            showStatus('Loaded file is missing required project fields (id, name, config).', true);
            return;
        }

        await applyProjectData(project, project.id || null);
        loadRecentProjects();
    } catch (e) {
        showStatus('Failed to load project: ' + e.message, true);
    }
}

async function createNewProject() {
    const name = document.getElementById('newProjectName').value.trim();
    const desc = document.getElementById('newProjectDesc').value.trim();

    if (!name) {
        showStatus('Project name is required', true);
        return;
    }

    if (!confirmDiscardUnsavedChanges('create and load a new project')) {
        return;
    }

    try {
        const resp = await fetch('/create_project', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, description: desc }),
        });
        const data = await resp.json();

        if (data.project_id) {
            document.getElementById('newProjectName').value = '';
            document.getElementById('newProjectDesc').value = '';
            showStatus(`Project "${name}" created successfully!`);
            loadRecentProjects();
            loadProjectAndSwitch(data.project_id);
        } else {
            showStatus(data.error, true);
        }
    } catch (e) {
        showStatus('Failed to create project: ' + e.message, true);
    }
}

async function loadProjectAndSwitch(projectId) {
    if (!projectId) {
        showStatus('Invalid project id', true);
        return;
    }

    if (projectLoadInProgress) {
        showStatus('A project is already loading. Please wait a moment.', true);
        return;
    }

    const actionLabel = projectId === lastProjectId ? 'reload this project' : 'switch projects';
    if (!confirmDiscardUnsavedChanges(actionLabel)) {
        return;
    }

    try {
        const resp = await fetch(`/load_project/${projectId}`);
        const project = await resp.json();

        if (!resp.ok) {
            const errMsg =
                project && project.error
                    ? project.error
                    : resp.status === 404
                      ? 'Project not found'
                      : 'Failed to load project';
            showStatus(errMsg, true);
            return;
        }

        await applyProjectData(project, projectId);
        await loadRecentProjects();
        showStatus(`Switched to project: ${project.name || project.id || projectId}`);
    } catch (e) {
        showStatus('Failed to load project: ' + e.message, true);
    }
}

async function saveCurrentProjectFromProjectsTab() {
    if (!lastProjectId) {
        showStatus('Load a project first, then save it.', true);
        return;
    }
    await handleSaveAndRun(false);
}

function applySavedAppConfig(cfg) {
    const options = cfg.app && cfg.app.options ? cfg.app.options : [];
    const unhandledArgs = [];
    const skippedDeprecated = [];

    for (let i = 0; i < options.length; i++) {
        const opt = options[i];
        if (currentDeprecatedFlags.has(opt)) {
            skippedDeprecated.push(opt);
            while (i + 1 < options.length && !options[i + 1].startsWith('-')) {
                i++;
            }
            continue;
        }

        const el = document.querySelector(`[data-flag="${opt}"]`);
        if (!el) {
            const multiEls = document.querySelectorAll(`.dynamic-opt-multi[data-flag="${opt}"]`);
            if (multiEls.length > 0) {
                while (i + 1 < options.length && !options[i + 1].startsWith('-')) {
                    i++;
                    const val = options[i];
                    document
                        .querySelectorAll(`.dynamic-opt-multi[data-flag="${opt}"][value="${val}"]`)
                        .forEach((checkedEl) => (checkedEl.checked = true));
                }
            } else if (opt.startsWith('-')) {
                unhandledArgs.push(opt);
                while (i + 1 < options.length && !options[i + 1].startsWith('-')) {
                    i++;
                    unhandledArgs.push(options[i]);
                }
            }
            continue;
        }

        if (el.classList.contains('dynamic-opt-bool')) {
            const isNegated = el.dataset.negated === 'true';
            el.value = isNegated ? 'disabled' : 'enabled';
        } else if (el.type === 'checkbox') {
            el.checked = true;
        } else if (i + 1 < options.length && !options[i + 1].startsWith('-')) {
            if (el.tagName === 'TEXTAREA') {
                const vals = [];
                while (i + 1 < options.length && !options[i + 1].startsWith('-')) {
                    i++;
                    vals.push(options[i]);
                }
                el.value = vals.join('\n');
            } else {
                el.value = options[i + 1];
                i++;
            }
        }

        if (typeof updateOptPreview === 'function') updateOptPreview(el);
    }

    document.getElementById('custom_args').value = unhandledArgs.join(' ');

    // Restore qsirecon recon-spec from saved options
    const reconSpecIdx = options.indexOf('--recon-spec');
    if (reconSpecIdx !== -1 && reconSpecIdx + 1 < options.length) {
        document.getElementById('qsirecon_recon_spec').value = options[reconSpecIdx + 1];
    }

    // Restore qsirecon atlases from saved options
    const atlasIdx = options.indexOf('--atlases');
    if (atlasIdx !== -1) {
        // Collect all non-flag values following --atlases
        const savedAtlases = new Set();
        for (let i = atlasIdx + 1; i < options.length && !options[i].startsWith('-'); i++) {
            savedAtlases.add(options[i]);
        }
        document.querySelectorAll('.qsirecon-atlas').forEach((cb) => {
            cb.checked = savedAtlases.has(cb.value);
        });
    }
    // (if --atlases not in saved options, keep the default checked state from HTML)

    updateQsireconUI();

    if (cfg.app && cfg.app.mounts) {
        // Separate out the managed /fssubjects mount from user-defined custom mounts
        const fsMountEntry = cfg.app.mounts.find(
            (m) => m.target === '/fssubjects:ro' || m.target === '/fssubjects',
        );
        const userMounts = cfg.app.mounts.filter(
            (m) => m.target !== '/fssubjects:ro' && m.target !== '/fssubjects',
        );
        const mountLines = userMounts.map((m) => `${m.source}:${m.target}`);
        document.getElementById('custom_mounts').value = mountLines.join('\n');
        document.getElementById('qsirecon_fs_subjects_dir').value = fsMountEntry
            ? fsMountEntry.source
            : '';
    } else {
        document.getElementById('custom_mounts').value = '';
        document.getElementById('qsirecon_fs_subjects_dir').value = '';
    }

    if (skippedDeprecated.length > 0) {
        const uniqueDeprecated = Array.from(new Set(skippedDeprecated));
        showStatus(`Ignored deprecated option(s): ${uniqueDeprecated.join(', ')}`, true);
    }

    if (options.length === 0 && document.querySelectorAll('[data-flag]').length > 0) {
        _showNoSavedOptionsNotice();
    }
}

function _showNoSavedOptionsNotice() {
    if (document.getElementById('no-saved-options-notice')) return;
    const container = document.getElementById('optionsContainer');
    if (!container) return;
    const notice = document.createElement('div');
    notice.id = 'no-saved-options-notice';
    notice.style.cssText =
        'background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:10px 14px;margin-bottom:12px;font-size:0.85rem;color:#856404;display:flex;justify-content:space-between;align-items:center;gap:8px;';
    notice.innerHTML =
        '<span><strong>No saved options found</strong> for this pipeline. Configure options below and click <strong>Save</strong> to preserve them for future loads.</span><button onclick="this.parentNode.remove()" style="background:none;border:none;font-size:1.2rem;line-height:1;cursor:pointer;color:#856404;padding:0 2px;">&#x2715;</button>';
    container.insertBefore(notice, container.firstChild);
}

function _isOptionHelpCacheUsable(cache, container, engine) {
    if (!cache || typeof cache !== 'object') return false;
    if ((cache.container || '') !== (container || '')) return false;
    if ((cache.engine || '') !== (engine || '')) return false;
    const payload = cache.payload;
    if (!payload || typeof payload !== 'object' || !Array.isArray(payload.sections)) return false;
    if ((payload.parser_version || 0) < _OPTION_CACHE_MIN_PARSER_VERSION) return false;
    return true;
}

function _getActivePipelineOptionHelpCache() {
    const entry = getCurrentPipelineEntry();
    const app = entry && entry.app && typeof entry.app === 'object' ? entry.app : null;
    if (!app) return null;
    return app.option_help_cache || null;
}

function _persistActivePipelineOptionHelpCache(cache) {
    if (!lastProjectId || !currentPipelineId || !currentProjectPipelines[currentPipelineId]) return;
    const existingEntry = normalizePipelineEntry(
        currentProjectPipelines[currentPipelineId] || {},
        currentPipelineId,
    );
    const app = cloneJson(existingEntry.app) || {};
    app.option_help_cache = cloneJson(cache);
    currentProjectPipelines[currentPipelineId] = {
        ...existingEntry,
        app,
    };
    syncCurrentProjectConfigWithPipelines();
}

// Opens the "App-Specific Arguments" accordion item without closing
// whichever section the user is currently in (e.g. "1. Container &
// Tools", where the "Load Options" button lives) -- deliberately
// does not go through initializeRunnerMainAccordion()'s closure-local
// openItem(), which closes every other section as a side effect and
// would yank the user away from what they were just looking at.
function _revealAppSpecificOptionsSection() {
    const section = document.getElementById('dynamicOptionsSection');
    const item = section && section.closest('.main-accordion-item');
    if (!item) return;
    item.classList.add('is-open');
    const header = item.querySelector('.main-accordion-header');
    if (header) header.setAttribute('aria-expanded', 'true');
    item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function _renderDynamicOptionsFromHelpData(
    data,
    container,
    {
        quietSuccess = false,
        detectedOutputResolution = null,
        detectedOutputResolutionInfo = null,
        restoredFromCache = false,
    } = {},
) {
    const defaultCheckedFlags = new Set();
    if (
        String(container || '')
            .toLowerCase()
            .includes('fmriprep')
    ) {
        defaultCheckedFlags.add('--fs-no-reconall');
    }

    const containerEl = document.getElementById('optionsContainer');
    document.getElementById('dynamicOptionsSection').style.display = 'block';
    _revealAppSpecificOptionsSection();

    if (data.app_info) document.getElementById('docLink').href = data.app_info.url;
    currentDeprecatedFlags = new Set(data.deprecated_flags || []);
    containerEl.innerHTML = '';

    if (data.sections && data.sections.length > 0) {
        data.sections.forEach((sec, idx) => {
            const secDiv = document.createElement('div');
            secDiv.className = 'opt-section' + (idx === 0 ? ' active' : '');
            const h = document.createElement('div');
            h.className = 'opt-section-header';
            h.innerHTML = `<span>${sec.title}</span><i class="fas fa-caret-down"></i>`;
            h.onclick = () => {
                secDiv.classList.toggle('active');
            };
            const content = document.createElement('div');
            content.className = 'opt-section-content';
            sec.options.forEach((opt) => {
                const item = document.createElement('div');
                item.className = 'p-3 border rounded bg-light d-flex flex-column';
                let input = '';
                const reqMark = opt.required ? ' <span class="text-danger">*</span>' : '';
                const reqAttr = opt.required ? ' data-required="true"' : '';
                const optDefaultValue =
                    opt.flag === '--output-resolution' && detectedOutputResolution
                        ? detectedOutputResolution
                        : '';

                if (opt.choices && opt.choices.length > 0) {
                    if (opt.is_multiple) {
                        let chks = '';
                        opt.choices.forEach((ch) => {
                            chks += `<div class="form-check"><input type="checkbox" class="form-check-input dynamic-opt-multi" data-flag="${opt.flag}" value="${ch}"> <label class="form-check-label">${ch}</label></div>`;
                        });
                        input = `<label class="form-label">${opt.name}${reqMark}</label><div${reqAttr} class="dynamic-opt-multi-wrap" data-flag="${opt.flag}" style="max-height:120px;overflow:auto;border:1px solid #dee2e6;padding:10px;background:white;border-radius:6px;">${chks}</div>`;
                    } else {
                        let opts = '<option value="">-- Default --</option>';
                        opt.choices.forEach(
                            (ch) => (opts += `<option value="${ch}">${ch}</option>`),
                        );
                        input = `<label class="form-label">${opt.name}${reqMark}</label><select class="dynamic-opt form-select" data-flag="${opt.flag}"${reqAttr} onchange="updateOptPreview(this)">${opts}</select>`;
                    }
                } else if (opt.has_value) {
                    if (opt.is_multiple) {
                        input = `<label class="form-label">${opt.name}${reqMark}</label><textarea class="dynamic-opt form-control" data-flag="${opt.flag}"${reqAttr} placeholder="Enter values (one per line)" oninput="updateOptPreview(this)"></textarea>`;
                    } else {
                        input = `<label class="form-label">${opt.name}${reqMark}</label><input type="text" class="dynamic-opt form-control" data-flag="${opt.flag}"${reqAttr} value="${optDefaultValue}" oninput="updateOptPreview(this)">`;
                    }
                } else {
                    const isDefaultChecked = defaultCheckedFlags.has(opt.flag);
                    let boolOptions = '';
                    if (opt.is_negated) {
                        boolOptions = `<option value="enabled"${!isDefaultChecked ? ' selected' : ''}>Enabled</option>
                                               <option value="disabled"${isDefaultChecked ? ' selected' : ''}>Disabled</option>`;
                    } else {
                        boolOptions = `<option value="disabled"${!isDefaultChecked ? ' selected' : ''}>Disabled</option>
                                               <option value="enabled"${isDefaultChecked ? ' selected' : ''}>Enabled</option>`;
                    }

                    input = `<label class="form-label">${opt.name}${reqMark}</label>
                                     <select class="dynamic-opt-bool form-select" data-flag="${opt.flag}" data-negated="${opt.is_negated}"${reqAttr} onchange="updateOptPreview(this)">
                                     ${boolOptions}
                                     </select>`;
                }

                const desc = opt.description
                    ? `<div style="font-size:0.7rem;color:#777;margin-top:10px;line-height:1.4;border-top:1px solid #eee;padding-top:8px;">${opt.description}</div>`
                    : '';
                const preview = `<div class="mt-2"><span class="badge bg-secondary opacity-50 font-monospace opt-preview" style="font-size:0.65rem;"></span></div>`;

                item.innerHTML = input + preview + desc;
                content.appendChild(item);

                const selectEl = item.querySelector(
                    '.dynamic-opt, .dynamic-opt-bool, .dynamic-opt-multi, input, textarea',
                );
                if (selectEl) updateOptPreview(selectEl);
            });
            secDiv.appendChild(h);
            secDiv.appendChild(content);
            containerEl.appendChild(secDiv);
        });

        if (detectedOutputResolution && detectedOutputResolutionInfo) {
            const zooms = (detectedOutputResolutionInfo.voxel_sizes_mm || []).join(' x ');
            showStatus(
                `Auto-filled --output-resolution from native DWI voxel size (${zooms} mm → ${detectedOutputResolution} mm).`,
            );
        }

        if (!quietSuccess) {
            endRunnerOptionsLoad('Container options are ready.');
        } else if (restoredFromCache) {
            endRunnerOptionsLoad('Container options restored from saved project cache.');
        } else {
            endRunnerOptionsLoad('Container options restored from project.');
        }
        return;
    }

    let msg = '<div class="text-center p-4">No specific options detected for this container.</div>';
    if (data.raw_help) {
        msg += `<div class="mt-3"><button class="btn btn-sm btn-outline-secondary" onclick="this.nextElementSibling.style.display='block'; this.style.display='none'">Show Raw Help Output</button>
                       <pre style="display:none; text-align:left; font-size:0.7rem; background:#f8f9fa; padding:15px; margin-top:10px; border:1px solid #eee; border-radius:5px; max-height:300px; overflow:auto;">${data.raw_help}</pre></div>`;
    }
    containerEl.innerHTML = msg;
    if (restoredFromCache) {
        endRunnerOptionsLoad('No specific options detected (restored from saved project cache).');
    } else {
        endRunnerOptionsLoad('No specific options detected for this container.');
    }
}

async function applyProjectData(project, projectIdOverride) {
    // Load project config into the form
    const cfg = project.config && typeof project.config === 'object' ? project.config : {};
    const pipelineState = ensureProjectPipelineState(cfg);
    currentProjectPipelines = pipelineState.pipelines;
    currentPipelineId = pipelineState.activePipeline;

    const activeEntry =
        currentProjectPipelines[currentPipelineId] ||
        normalizePipelineEntry({}, 'Default Pipeline');
    const common =
        activeEntry.common && typeof activeEntry.common === 'object' ? activeEntry.common : {};
    const app = activeEntry.app && typeof activeEntry.app === 'object' ? activeEntry.app : {};
    const loadToken = ++activeProjectLoadToken;

    // Store project info early so navbar updates immediately
    lastProjectId = projectIdOverride || project.id || '';
    // The project we just loaded already has a project.json on disk at this
    // deterministic path -- treat it as "saved" immediately (hasUnsavedProjectChanges()
    // separately guards against acting on subsequent unsaved edits).
    lastSavedPath = `projects/${lastProjectId}/project.json`;
    setNavbarProjectName(project.name || project.id || '');
    currentProjectConfig = cloneJson(cfg) || {};
    syncCurrentProjectConfigWithPipelines();
    loadedProjectMeta = {
        id: lastProjectId,
        name: project.name || project.id || '',
        description: project.description || '',
        last_modified: project.last_modified || '',
    };
    lastProjectSavedAt = project.last_modified || '';
    lastProjectSavePath = lastProjectId ? `projects/${lastProjectId}/project.json` : '';
    setProjectDirty(false);
    setProjectLoadInProgress(true);
    initialLoad = true;
    setTerminalProjectContextMessage(
        `[Loading logs for project: ${project.name || project.id || projectIdOverride || 'project'}]\n`,
    );
    updateHpcConfigDetails(currentProjectConfig);
    renderPipelinePresetSelector();

    suppressProjectDirtyTracking = true;
    try {
        // Fill form fields
        document.getElementById('bids_folder').value = common.bids_folder || '';
        updateCohortDatasetDisplay(common.bids_folder || '');
        switchBidsSourceMode(inferBidsSourceMode(common.bids_folder));
        document.getElementById('output_folder').value = common.output_folder || '';
        document.getElementById('tmp_folder').value = common.tmp_folder || '';
        document.getElementById('templateflow_dir').value = common.templateflow_dir || '';
        document.getElementById('fs_license_file').value = common.fs_license_file || '';
        const inferredRoot = _derivePipelineRootFromOutput(
            common.output_folder || '',
            common.pipeline_app_name || '',
            common.pipeline_version || '',
        );
        document.getElementById('pipeline_output_root').value =
            common.pipeline_output_root || inferredRoot || '';
        document.getElementById('pipeline_app_name').value = common.pipeline_app_name || '';
        document.getElementById('pipeline_version').value = common.pipeline_version || '';
        document.getElementById('pipeline_auto_versioning').checked =
            common.pipeline_auto_versioning === true;
        document.getElementById('notify_email').value = common.notify_email || '';
        document.getElementById('jobs').value = common.jobs || 1;
        document.getElementById('analysis_level').value = app.analysis_level || 'participant';
        syncRunnerSubjectFilterWithAnalysisLevel();
        const fastsurferLongitudinalEl = document.getElementById('fastsurfer_longitudinal');
        if (fastsurferLongitudinalEl) {
            // See the matching comment in applyPipelineEntryToRunnerForm:
            // either signal (execution_adapter or hpc.preset) counts,
            // so an explicit Compute Preset choice can't get silently
            // reverted by a later save that only looked at the
            // checkbox's (stale) restored state.
            const presetSaysLongitudinal = cfg && cfg.hpc && cfg.hpc.preset === 'fastsurfer_bids';
            fastsurferLongitudinalEl.checked =
                app.execution_adapter === 'fastsurfer-bids' || !!presetSaysLongitudinal;
        }
        document.getElementById('container_engine').value = common.container_engine || 'apptainer';

        // Load saved Check Output settings when present
        const validation = currentProjectConfig.validation || {};
        document.getElementById('validation_bids_folder').value =
            validation.bids_folder || common.bids_folder || '';
        document.getElementById('validation_derivatives_folder').value =
            validation.derivatives_folder || common.output_folder || '';
        document.getElementById('validation_pipeline').value = validation.pipeline || '';
        document.getElementById('validation_verbose').checked = !!validation.verbose;
        document.getElementById('validation_quiet').checked = !!validation.quiet;
        document.getElementById('validation_missing_only').checked = !!validation.list_missing;
        scheduleCheckPreflightRefresh();
        scheduleBuildPreflightRefresh();

        // Load container path based on engine
        const engine = common.container_engine || 'apptainer';
        if (engine === 'docker') {
            // Parse docker image (e.g., "nipreps/fmriprep:23.1.0")
            const containerImage = common.container || '';
            const parts = containerImage.split(':');
            const savedTag = parts[1] || 'latest';
            document.getElementById('container_docker_repo').value = parts[0] || '';
            // Seed the saved tag into the <select> — setting .value on an empty
            // select is silently ignored, so the option must exist first.
            const tagSelectB = document.getElementById('container_docker_tag');
            if (!Array.from(tagSelectB.options).some((o) => o.value === savedTag)) {
                tagSelectB.innerHTML = '';
                tagSelectB.add(new Option(savedTag, savedTag));
            }
            tagSelectB.value = savedTag;

            // Show loading placeholder immediately so the user sees feedback
            // while the background help-fetch (fetchAppOptions) runs.
            const _cachedHelpForPreview =
                app && app.option_help_cache ? app.option_help_cache : null;
            const _cacheUsable = _isOptionHelpCacheUsable(
                _cachedHelpForPreview,
                common.container || '',
                'docker',
            );
            if (!_cacheUsable) {
                const _sec = document.getElementById('dynamicOptionsSection');
                const _ctr = document.getElementById('optionsContainer');
                if (_sec && _ctr) {
                    _sec.style.display = 'block';
                    _ctr.innerHTML =
                        '<div class="text-center p-5"><div class="spinner-border text-primary" role="status"></div><div class="mt-2 text-muted small">Loading options for ' +
                        (parts[0] || 'container') +
                        ':' +
                        savedTag +
                        '...</div></div>';
                }
            }
        } else {
            // Parse apptainer path (e.g., "/path/to/container.sif")
            const containerPath = common.container || '';
            if (containerPath) {
                const folder = containerPath.substring(0, containerPath.lastIndexOf('/'));
                document.getElementById('container_folder').value = folder;
            }
        }

        // Store container path (before applying machine defaults below) so
        // that a project with its own saved container is never mistaken for
        // "no container configured yet" and overwritten with the machine's
        // default image while container_select is still being populated.
        currentContainerPath = common.container || '';
        containerLocked = common.container_locked || false;

        toggleEngineFields();
        applyMachineSettingsToRunnerForm({ silent: true, fillOnlyEmpty: true });
        applyVersionedOutputPaths({ source: 'project-load' });
    } finally {
        suppressProjectDirtyTracking = false;
        setProjectLoadInProgress(false);
    }

    // Restore heavy container metadata/options in background to keep project switch responsive.
    void restoreProjectLoadDetails({ app }, common, loadToken);
    showStatus(
        `Loaded project: ${project.name || project.id || 'project'} (pipeline: ${getCurrentPipelineName() || currentPipelineId})`,
    );
}

async function restoreProjectLoadDetails(cfg, common, loadToken) {
    try {
        if (isStaleProjectLoad(loadToken)) return;

        const engine = common.container_engine || 'apptainer';
        if (engine === 'apptainer') {
            const containerPath = common.container || '';
            if (containerPath) {
                const filename = containerPath.substring(containerPath.lastIndexOf('/') + 1);
                const scanned = await scanContainers(loadToken, { silent: true });
                if (!scanned || isStaleProjectLoad(loadToken)) return;
                const selectEl = document.getElementById('container_select');
                if (selectEl) {
                    selectEl.value = filename;
                }
                await checkAppVersion(loadToken);
            }
        }

        if (isStaleProjectLoad(loadToken)) return;
        const shouldLoadOptions = !!common.container;
        if (shouldLoadOptions) {
            const engine = common.container_engine || 'apptainer';
            const cachedHelp =
                cfg && cfg.app && cfg.app.option_help_cache ? cfg.app.option_help_cache : null;
            await fetchAppOptions(loadToken, {
                quietSuccess: true,
                cachedHelp: _isOptionHelpCacheUsable(cachedHelp, common.container || '', engine)
                    ? cachedHelp
                    : null,
            });
            if (isStaleProjectLoad(loadToken)) return;
            applySavedAppConfig(cfg);
        }

        // Re-run the advisory HPC pre-fill now that container_select/
        // pipeline_app_name have their real values -- the earlier call
        // from applyProjectData() fires before this async restore
        // finishes, so app detection (inferCurrentPipelineAppName())
        // can miss and silently fall back to the generic defaults.
        if (!isStaleProjectLoad(loadToken)) {
            updateHpcConfigDetails(currentProjectConfig);
            // container_select's value above was set programmatically
            // (selectEl.value = ...), which doesn't fire its onchange
            // handler -- finalize which Load-Options button shows now
            // that the restore has settled. Deliberately the button-
            // only variant, not the full recompute: fetchAppOptions()
            // (already awaited above, inside shouldLoadOptions) already
            // correctly resolved the stale-banner state for this load;
            // re-deriving it again here was observed re-showing a
            // banner it shouldn't have.
            _updateFetchHelpBtnOnly();
        }
    } catch (e) {
        console.warn('Background project restore failed:', e);
    }
}

async function deleteProject(projectId, projectName = '') {
    if (!projectId) {
        showStatus('Invalid project id', true);
        return;
    }

    const displayName = projectName || projectId;
    let prompt = `Delete project "${displayName}" permanently?\n\nThis removes:\n- project.json\n- project logs\n- run state/history\n\nThis action cannot be undone.`;
    if (projectId === lastProjectId && hasUnsavedProjectChanges()) {
        prompt = `You have unsaved changes in "${displayName}".\n\n` + prompt;
    }
    if (!confirm(prompt)) return;

    try {
        const resp = await fetch(`/delete_project/${projectId}`, { method: 'DELETE' });
        const data = await resp.json();

        if (resp.ok) {
            showStatus('Project deleted successfully');
            loadRecentProjects();
            if (projectId === lastProjectId) {
                lastProjectId = '';
                loadedProjectMeta = null;
                currentProjectPipelines = {};
                currentPipelineId = '';
                lastProjectSavedAt = '';
                lastProjectSavePath = '';
                lastSavedPath = '';
                currentProjectConfig = null;
                setProjectDirty(false);
                setNavbarProjectName('');
                renderPipelinePresetSelector();
                setTerminalProjectContextMessage(
                    '[Load a project to view project-specific logs.]\n',
                );
                scheduleCheckPreflightRefresh();
                scheduleBuildPreflightRefresh();
            }
        } else {
            showStatus(data.error, true);
        }
    } catch (e) {
        showStatus('Failed to delete project: ' + e.message, true);
    }
}

async function checkAppVersion(loadToken = null) {
    const requestId = ++checkAppVersionRequestId;
    const f = document.getElementById('container_folder').value;
    const c = document.getElementById('container_select').value;
    if (!c) return;
    if (isStaleProjectLoad(loadToken)) return;
    const alertEl = document.getElementById('versionAlert');
    alertEl.style.display = 'none';

    try {
        const resp = await fetch('/check_container_version', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ container: f + '/' + c }),
        });
        const data = await resp.json();
        if (requestId !== checkAppVersionRequestId || isStaleProjectLoad(loadToken)) return;
        if (data.is_newer) {
            alertEl.innerHTML = `<span style="color: #e67e22; font-weight: bold;"><i class="fas fa-exclamation-triangle me-1"></i> Update: <a href="${data.changelog_url}" target="_blank" style="color: #d35400;">${data.latest}</a> available</span>`;
            alertEl.style.display = 'block';
        } else if (data.latest) {
            alertEl.innerHTML = `<span style="color: #27ae60; font-weight: bold;"><i class="fas fa-check-circle me-1"></i> Vers. ${data.latest} up-to-date</span>`;
            alertEl.style.display = 'block';
        }
    } catch (e) {}
    updateQsireconUI();
    applyVersionedOutputPaths({ source: 'check-app-version' });
}

function toggleEngineFields() {
    const engine = document.getElementById('container_engine').value;
    const apptainerFields = document.getElementById('apptainer_fields_container');
    const dockerFields = document.getElementById('docker_fields_container');

    if (engine === 'docker') {
        apptainerFields.style.display = 'none';
        dockerFields.style.display = 'block';
    } else {
        apptainerFields.style.display = 'block';
        dockerFields.style.display = 'none';
    }
    updateQsireconUI();
    applyVersionedOutputPaths({ source: 'engine-toggle' });
    updateFetchHelpBtnVisibility();
    scheduleRunnerPreflightRefresh();
}

async function scanContainers(loadToken = null, options = {}) {
    const silent = !!options.silent;
    applyVersionedOutputPaths({ source: 'machine-settings' });
    const f = document.getElementById('container_folder').value;
    if (!f) {
        if (!silent) showStatus('Please specify the containers folder first.', true);
        scheduleRunnerPreflightRefresh();
        return false;
    }
    if (isStaleProjectLoad(loadToken)) return false;
    try {
        const resp = await fetch('/list_containers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder: f }),
        });
        const data = await resp.json();
        if (isStaleProjectLoad(loadToken)) return false;
        if (data.error) {
            if (!silent) showStatus(data.error, true);
            return false;
        }

        const s = document.getElementById('container_select');
        s.innerHTML = '';
        if (data.containers && data.containers.length > 0) {
            data.containers.forEach((c) => s.add(new Option(c, c)));
            await checkAppVersion(loadToken);
            applyVersionedOutputPaths({ source: 'scan-containers' });
            // Skip during a project/pipeline load restore (loadToken set): the
            // select was just repopulated and defaults to its first (sorted)
            // entry, not the saved container, so this comparison would flag a
            // container the user never picked. The caller immediately corrects
            // the selection and calls fetchAppOptions(), which settles the
            // banner against the real, final container.
            if (loadToken === null) updateFetchHelpBtnVisibility();
            scheduleRunnerPreflightRefresh();
            return true;
        } else {
            if (!silent) showStatus('No containers (.sif/.simg) found in that folder.', true);
            scheduleRunnerPreflightRefresh();
            return false;
        }
    } catch (e) {
        console.error('Scan error:', e);
        if (!silent) showStatus('Failed to scan containers.', true);
        scheduleRunnerPreflightRefresh();
        return false;
    }
}

async function fetchDockerRepoTags() {
    const repo = document.getElementById('container_docker_repo').value.trim();
    const tagSelect = document.getElementById('container_docker_tag');

    if (!repo) {
        showStatus('Please enter a Docker Hub repository (e.g. nipreps/fmriprep)', true);
        return;
    }

    tagSelect.innerHTML = '<option>Loading...</option>';
    try {
        const resp = await fetch('/get_docker_tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo: repo }),
        });
        const data = await resp.json();

        if (data.tags && data.tags.length > 0) {
            tagSelect.innerHTML = '';
            data.tags.forEach((tag) => {
                tagSelect.add(new Option(tag, tag));
            });
            showStatus(`Fetched ${data.tags.length} tags for ${repo}`);
            applyVersionedOutputPaths({ source: 'docker-tags' });
            scheduleRunnerPreflightRefresh();
        } else if (data.error) {
            showStatus(data.error, true);
            tagSelect.innerHTML = '<option value="latest">latest (fallback)</option>';
            applyVersionedOutputPaths({ source: 'docker-tags-fallback' });
            scheduleRunnerPreflightRefresh();
        }
    } catch (e) {
        showStatus('Failed to fetch Docker tags', true);
        tagSelect.innerHTML = '<option value="latest">latest (fallback)</option>';
        scheduleRunnerPreflightRefresh();
    }
}

async function pullDockerImage() {
    const repo = document.getElementById('container_docker_repo').value.trim();
    const tag = document.getElementById('container_docker_tag').value;
    if (!repo || !tag) {
        showStatus('Select a repository and tag first', true);
        return;
    }
    const image = `${repo}:${tag}`;

    // Visual feedback
    const pullBtn = document.getElementById('pullDockerBtn');
    const originalText = pullBtn ? pullBtn.innerHTML : 'Pull';
    if (pullBtn) {
        pullBtn.disabled = true;
        pullBtn.innerHTML =
            '<span class="spinner-border spinner-border-sm me-1"></span> Pulling...';
    }

    showStatus(`Initiating pull for ${image}. Check the console below for progress.`);

    try {
        const resp = await fetch('/pull_image', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: image, engine: 'docker' }),
        });
        const data = await resp.json();
        if (data.message) {
            showStatus(data.message + ' Check Console Output for status.');
        } else {
            showStatus(data.error, true);
            if (pullBtn) {
                pullBtn.disabled = false;
                pullBtn.innerHTML = originalText;
            }
        }
    } catch (e) {
        showStatus('Failed to request image pull', true);
        if (pullBtn) {
            pullBtn.disabled = false;
            pullBtn.innerHTML = originalText;
        }
    } finally {
        // We keep it disabled for a bit to prevent double clicks, but re-enable after timeout
        // or just let the user know it's a background process.
        setTimeout(() => {
            if (pullBtn) {
                pullBtn.disabled = false;
                pullBtn.innerHTML = originalText;
            }
        }, 5000);
    }
}

// Single source of truth for "which container is currently selected,"
// shared by fetchAppOptions() and updateFetchHelpBtnVisibility() so
// the two can never disagree about what "the current container" means.
// Returns null if the engine's fields aren't fully filled in yet.
function _getSelectedContainerRef() {
    const engine = document.getElementById('container_engine').value;
    if (engine === 'docker') {
        const repo = document.getElementById('container_docker_repo').value.trim();
        const tag = document.getElementById('container_docker_tag').value;
        if (!repo || !tag) return null;
        return { engine, container: `${repo}:${tag}` };
    }
    const f = document.getElementById('container_folder').value;
    const c = document.getElementById('container_select').value;
    if (!c) return null;
    return { engine, container: f + '/' + c };
}

// Keeps the "Load Options" button available whenever a container is
// selected (it's a manual force-refresh escape hatch -- e.g. after
// rebuilding a .sif with new flags, the cache would still look
// "usable" even though the actual --help output changed, so this
// must never be hidden just because the cache matches), and reactively
// shows a stale-options banner when the selected container no longer
// matches what's actually loaded (currentOptionHelpCache) -- e.g.
// after picking a different image from the dropdown, which otherwise
// leaves the previous container's options displayed with no
// indication they're stale. Called on container_select/
// container_docker_tag change, on engine toggle, and after a
// successful fetchAppOptions() (to clear the banner again).
// Shows the right "Load Options" button for the current engine.
// Split out from the staleness recompute below because this part
// never depends on the (evidently still fragile in some code path)
// container-string comparison -- the button is always available as
// a manual force-refresh once a container is selected, full stop.
function _updateFetchHelpBtnOnly() {
    const apptainerBtn = document.getElementById('fetchHelpBtn');
    const dockerBtn = document.getElementById('fetchHelpBtnDocker');
    const ref = _getSelectedContainerRef();

    if (!ref) {
        if (apptainerBtn) apptainerBtn.style.display = 'none';
        if (dockerBtn) dockerBtn.style.display = 'none';
        return;
    }

    const activeBtn = ref.engine === 'docker' ? dockerBtn : apptainerBtn;
    const inactiveBtn = ref.engine === 'docker' ? apptainerBtn : dockerBtn;
    if (inactiveBtn) inactiveBtn.style.display = 'none';
    if (activeBtn) activeBtn.style.display = 'block';
}

// Full reactive check, including the stale-options banner recompute
// -- use this from genuine container-switch events (container_select/
// container_docker_tag onchange, engine toggle, scanContainers
// success). Deliberately NOT called from the post-project-load
// "settle" step in restoreProjectLoadDetails any more: that step ran
// immediately after fetchAppOptions() already correctly resolved and
// hid the banner for a load that just succeeded, and re-running this
// comparison there was observed re-showing a stale banner it
// shouldn't have -- root cause not fully pinned down, but there's
// nothing for this recompute to legitimately catch at that point
// that fetchAppOptions' own success path didn't already handle.
function updateFetchHelpBtnVisibility() {
    _updateFetchHelpBtnOnly();
    const staleNotice = document.getElementById('staleOptionsNotice');
    const ref = _getSelectedContainerRef();

    if (!ref) {
        if (staleNotice) staleNotice.style.display = 'none';
        return;
    }

    const usable = _isOptionHelpCacheUsable(currentOptionHelpCache, ref.container, ref.engine);
    // Only flag existing rendered options as stale -- don't show the
    // "different container" banner over an empty/not-yet-loaded panel.
    const hasRenderedOptions =
        document.getElementById('dynamicOptionsSection').style.display !== 'none';
    if (staleNotice) staleNotice.style.display = !usable && hasRenderedOptions ? 'flex' : 'none';
}

// Unconditionally hides the stale-options banner -- used right after
// a render that just succeeded for the *currently selected*
// container (both the cache-hit and fresh-fetch paths in
// fetchAppOptions), where by construction there is nothing stale to
// flag. Deliberately not a call to updateFetchHelpBtnVisibility()'s
// recompute-and-compare here: that path re-derives the container
// reference from live DOM state independently of what was just
// fetched, which is the right check for a genuine container-switch
// event but an unnecessary (and, if anything about those two
// derivations ever drifts, misleading) extra step immediately after
// a load that unambiguously just matched.
function _hideStaleOptionsNotice() {
    const staleNotice = document.getElementById('staleOptionsNotice');
    if (staleNotice) staleNotice.style.display = 'none';
}

async function fetchAppOptions(loadToken = null, options = {}) {
    const quietSuccess = !!options.quietSuccess;
    const cachedHelp = options.cachedHelp || null;
    if (isStaleProjectLoad(loadToken)) return;
    const ref = _getSelectedContainerRef();
    const engine = ref ? ref.engine : document.getElementById('container_engine').value;
    let container;
    if (engine === 'docker') {
        const repo = document.getElementById('container_docker_repo').value.trim();
        const tag = document.getElementById('container_docker_tag').value;
        if (!repo || !tag) {
            showStatus('Please enter a Docker repository and select a tag first.', true);
            return;
        }
        container = `${repo}:${tag}`;
    } else {
        const f = document.getElementById('container_folder').value;
        const c = document.getElementById('container_select').value;
        if (!c) return;
        container = f + '/' + c;
    }
    if (!container) return;

    // Show the spinner and preflight loading indicator immediately —
    // before any async work — so the user sees feedback right away.
    const containerEl = document.getElementById('optionsContainer');
    document.getElementById('dynamicOptionsSection').style.display = 'block';
    _revealAppSpecificOptionsSection();
    containerEl.innerHTML =
        '<div class="text-center p-5"><div class="spinner-border text-primary" role="status"></div><div class="mt-2">Analyzing container...</div></div>';
    beginRunnerOptionsLoad(`Loading options from ${container}`);

    // Per-app defaults for boolean flags.
    // These defaults apply only to dynamically-discovered (help-parsed) options.
    const defaultCheckedFlags = new Set();
    if (container.toLowerCase().includes('fmriprep')) {
        defaultCheckedFlags.add('--fs-no-reconall');
    }

    let detectedOutputResolution = null;
    let detectedOutputResolutionInfo = null;
    const bidsFolder = document.getElementById('bids_folder').value.trim();

    try {
        if (isStaleProjectLoad(loadToken)) {
            endRunnerOptionsLoad('');
            return;
        }

        // Cache hit path: render immediately — no DWI check needed because
        // applySavedAppConfig will restore the user's saved output-resolution.
        if (_isOptionHelpCacheUsable(cachedHelp, container, engine)) {
            currentOptionHelpCache = cloneJson(cachedHelp);
            _renderDynamicOptionsFromHelpData(cachedHelp.payload, container, {
                quietSuccess,
                detectedOutputResolution: null,
                detectedOutputResolutionInfo: null,
                restoredFromCache: true,
            });
            _hideStaleOptionsNotice();
            return;
        }

        // Fresh fetch: run DWI native-resolution check first so the detected
        // resolution is available for output-resolution auto-fill when rendering.
        if (bidsFolder) {
            try {
                const nativeResp = await fetch('/get_dwi_native_resolution', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bids_dir: bidsFolder }),
                });
                const nativeData = await nativeResp.json();
                if (nativeResp.ok && nativeData.resolution_mm) {
                    detectedOutputResolution = String(nativeData.resolution_mm);
                    detectedOutputResolutionInfo = nativeData;
                }
            } catch (e) {
                console.warn('Could not detect native DWI resolution:', e);
            }
        }

        if (engine === 'apptainer') checkAppVersion(loadToken);
        const resp = await fetch('/get_app_help', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ container: container, container_engine: engine }),
        });
        const data = await resp.json();
        if (isStaleProjectLoad(loadToken)) {
            endRunnerOptionsLoad('');
            return;
        }

        if (data.error) {
            if (data.need_pull) {
                containerEl.innerHTML = `
                            <div class="alert alert-info m-4 shadow-sm border-info" style="border-left: 5px solid;">
                                <h5 class="alert-heading"><i class="fas fa-download me-2"></i> Local Image Check</h5>
                                <p>${data.error}</p>
                                <hr>
                                <button class="btn btn-primary btn-sm" onclick="pullDockerImage()">
                                    <i class="fas fa-arrow-down me-1"></i> Pull Image Now
                                </button>
                            </div>`;
            } else {
                containerEl.innerHTML = `<div class="alert alert-warning m-4">Error fetching help: ${data.error}</div>`;
            }
            // We just failed to load options for *this* container, so a
            // leftover "these options are for a different container" banner
            // from before this call would be actively misleading here.
            _hideStaleOptionsNotice();
            endRunnerOptionsLoad(data.error || 'Failed to load options.', true);
            return;
        }

        currentOptionHelpCache = {
            container,
            engine,
            timestamp: new Date().toISOString(),
            payload: {
                app_info: data.app_info || null,
                deprecated_flags: data.deprecated_flags || [],
                sections: data.sections || [],
                raw_help: data.raw_help || '',
                parser_version: data.parser_version || 1,
            },
        };
        _persistActivePipelineOptionHelpCache(currentOptionHelpCache);

        // Silently persist cache to disk so the next project load is instant.
        if (lastProjectId && currentPipelineId) {
            fetch(`/patch_option_cache/${encodeURIComponent(lastProjectId)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    pipeline_id: currentPipelineId,
                    cache: currentOptionHelpCache,
                }),
            }).catch(() => {});
        }

        _renderDynamicOptionsFromHelpData(currentOptionHelpCache.payload, container, {
            quietSuccess,
            detectedOutputResolution,
            detectedOutputResolutionInfo,
            restoredFromCache: false,
        });
        _hideStaleOptionsNotice();

        // Restore saved option values into the freshly-rendered form.
        // This matters when the user pulls a Docker image and clicks "Load Options"
        // manually — restoreProjectLoadDetails only calls applySavedAppConfig once
        // on project load, so we need to re-apply here after a fresh help fetch.
        if (
            currentPipelineId &&
            currentProjectPipelines &&
            currentProjectPipelines[currentPipelineId]
        ) {
            const savedPipelineApp = (currentProjectPipelines[currentPipelineId] || {}).app;
            if (savedPipelineApp) {
                applySavedAppConfig({ app: savedPipelineApp });
            }
        }
    } catch (e) {
        containerEl.innerHTML = `<div class="alert alert-danger m-4">Connection error. Could not retrieve container help.</div>`;
        _hideStaleOptionsNotice();
        endRunnerOptionsLoad('Could not retrieve container options.', true);
    } finally {
        scheduleRunnerPreflightRefresh();
    }
}
