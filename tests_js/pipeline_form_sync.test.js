import { describe, it, expect, beforeEach } from 'vitest';
import { loadScript } from './helpers/loadScript.js';
import { executionParamsFixtureHtml } from './helpers/domFixture.js';

// pipeline_form_sync.js depends on a handful of helpers/state that live
// inline in templates/index.html (not extractable into a plain static/js
// file): isQsireconContainer(), inferCurrentPipelineAppName(),
// applyVersionedOutputPaths(), and the QSIRECON_CONNECTIVITY_WORKFLOWS set.
// Stubbed here per-test; everything else it calls (cloneJson,
// normalizePipelineEntry, ensureProjectPipelineState,
// renderPipelinePresetSelector, syncCurrentProjectConfigWithPipelines,
// _isOptionHelpCacheUsable) is the REAL project_loader.js implementation,
// loaded for real below.
function stubInlineGlobals({ isQsirecon = false, pipelineAppName = '' } = {}) {
    window.isQsireconContainer = () => isQsirecon;
    window.inferCurrentPipelineAppName = () => pipelineAppName;
    window.applyVersionedOutputPaths = () => false;
    window.QSIRECON_CONNECTIVITY_WORKFLOWS = new Set(['mrtrix_multishell_msmt_connectivity']);
}

beforeEach(() => {
    document.body.innerHTML = executionParamsFixtureHtml();
    loadScript('project_loader.js');
    loadScript('subregion_segmentation.js');
    stubInlineGlobals();
    loadScript('pipeline_form_sync.js');

    // Shared state pipeline_form_sync.js reads/writes as bare globals (see
    // its own header comment) -- normally `var`-declared in the inline
    // <script>; set directly here the same way that script initializes them.
    window.currentContainerPath = '';
    window.currentProjectPipelines = {};
    window.currentPipelineId = '';
    window.lastProjectId = '';
    window.currentProjectConfig = {};
});

describe('syncActivePipelineFromForm', () => {
    it('builds common/app from the form when no project is loaded', () => {
        const result = syncActivePipelineFromForm();

        expect(result.common.output_folder).toBe('/data/derivatives/app');
        expect(result.common.bids_folder).toBe('/data/bids');
        expect(result.common.container).toBe('/containers/app.sif');
        expect(result.app.analysis_level).toBe('participant');
        expect(result.missingRequired).toEqual([]);
        expect(result.missingReconSpec).toBe(false);
    });

    it('does not touch currentProjectPipelines when no project is loaded', () => {
        syncActivePipelineFromForm();
        expect(window.currentProjectPipelines).toEqual({});
    });

    // Regression test for the real incident this module was extracted to
    // fix: "Save HPC settings" used to clone currentProjectConfig directly
    // with no equivalent of this sync step, so a pipeline's
    // output_folder/pipeline_app_name/container could still be stale there
    // even though hpc.* itself saved fine. Cohort Setup then read that
    // stale snapshot and operated on the wrong dataset.
    it('syncs the form into BOTH currentProjectPipelines and currentProjectConfig when a project is loaded', () => {
        window.lastProjectId = 'proj1';
        window.currentPipelineId = 'qsiprep';
        window.currentProjectPipelines = {
            qsiprep: { name: 'qsiprep', common: { output_folder: '/stale/path' }, app: {} },
        };
        window.currentProjectConfig = {
            pipelines: cloneJson(window.currentProjectPipelines),
            active_pipeline: 'qsiprep',
            hpc: { partition: 'hpc', time: '24:00:00' },
        };

        syncActivePipelineFromForm();

        expect(window.currentProjectPipelines.qsiprep.common.output_folder).toBe('/data/derivatives/app');
        // The actual bug: currentProjectConfig used to stay at whatever it
        // was before this call -- must now reflect the freshly-synced entry.
        expect(window.currentProjectConfig.common.output_folder).toBe('/data/derivatives/app');
        expect(window.currentProjectConfig.pipelines.qsiprep.common.output_folder).toBe('/data/derivatives/app');
        // hpc settings, which this function never touches, must survive untouched.
        expect(window.currentProjectConfig.hpc).toEqual({ partition: 'hpc', time: '24:00:00' });
    });

    it('reports missingRequired for an unset required dynamic option, without aborting', () => {
        document.body.insertAdjacentHTML(
            'beforeend',
            '<select class="dynamic-opt" data-flag="--participant-label" data-required="true"><option value="">--</option></select>'
        );

        const result = syncActivePipelineFromForm();

        expect(result.missingRequired).toEqual(['--participant-label']);
        // Must still return a usable common/app -- callers decide whether to abort.
        expect(result.common.output_folder).toBe('/data/derivatives/app');
    });

    it('collects checked dynamic options (including multi-value textarea splitting) into app.options', () => {
        document.body.insertAdjacentHTML(
            'beforeend',
            `<textarea class="dynamic-opt" data-flag="--session_label">1,2 3</textarea>
             <input type="checkbox" class="dynamic-opt" data-flag="--skip-bids-validation" checked>`
        );

        const result = syncActivePipelineFromForm();

        expect(result.app.options).toContain('--skip-bids-validation');
        expect(result.app.options).toEqual(
            expect.arrayContaining(['--session_label', '1', '2', '3'])
        );
    });

    it('reports missingReconSpec for a QSIRecon container with no recon-spec chosen', () => {
        stubInlineGlobals({ isQsirecon: true, pipelineAppName: 'qsirecon' });

        const result = syncActivePipelineFromForm();

        expect(result.missingReconSpec).toBe(true);
    });

    it('injects --recon-spec and --fs-subjects-dir + bind mount for QSIRecon, stripping duplicates', () => {
        stubInlineGlobals({ isQsirecon: true, pipelineAppName: 'qsirecon' });
        document.getElementById('qsirecon_recon_spec').value = 'mrtrix_multishell_msmt';
        document.getElementById('qsirecon_fs_subjects_dir').value = '/data/derivatives/freesurfer';
        // A stray, already-present --recon-spec must be replaced, not duplicated.
        document.getElementById('custom_args').value = '--recon-spec old_spec';

        const result = syncActivePipelineFromForm();

        const reconSpecCount = result.app.options.filter((o) => o === '--recon-spec').length;
        expect(reconSpecCount).toBe(1);
        expect(result.app.options).toEqual(
            expect.arrayContaining(['--recon-spec', 'mrtrix_multishell_msmt'])
        );
        expect(result.app.options).toEqual(
            expect.arrayContaining(['--fs-subjects-dir', '/fssubjects'])
        );
        expect(result.app.mounts).toContainEqual({
            source: '/data/derivatives/freesurfer',
            target: '/fssubjects:ro',
        });
    });

    it('marks the container unlocked when the container path changed, locked when unchanged', () => {
        window.currentContainerPath = '/containers/old.sif';
        const first = syncActivePipelineFromForm();
        expect(first.common.container_locked).toBe(false);
        expect(window.currentContainerPath).toBe('/containers/app.sif');

        const second = syncActivePipelineFromForm();
        expect(second.common.container_locked).toBe(true);
    });

    it('sets execution_adapter for fastsurfer based on the longitudinal checkbox', () => {
        stubInlineGlobals({ pipelineAppName: 'fastsurfer' });
        document.getElementById('fastsurfer_longitudinal').checked = true;
        expect(syncActivePipelineFromForm().app.execution_adapter).toBe('fastsurfer-bids');

        document.getElementById('fastsurfer_longitudinal').checked = false;
        expect(syncActivePipelineFromForm().app.execution_adapter).toBe('fastsurfer-cross');
    });

    it('sets execution_adapter for freesurfer only when the BIDS-longitudinal checkbox is checked', () => {
        stubInlineGlobals({ pipelineAppName: 'freesurfer' });
        document.getElementById('freesurfer_bids_longitudinal').checked = true;
        expect(syncActivePipelineFromForm().app.execution_adapter).toBe('freesurfer-bids');

        document.getElementById('freesurfer_bids_longitudinal').checked = false;
        expect(syncActivePipelineFromForm().app.execution_adapter).toBeUndefined();
    });

    it('carries subregion_segmentation config through from the real subregion_segmentation.js module', () => {
        stubInlineGlobals({ pipelineAppName: 'freesurfer' });
        document.getElementById('subregion_segmentation_enabled')?.remove();
        document.body.insertAdjacentHTML(
            'beforeend',
            `<input type="checkbox" id="subregion_segmentation_enabled" checked>
             <input type="checkbox" id="subregion_structure_thalamus" checked>
             <input type="checkbox" id="subregion_structure_hippo_amygdala">
             <input type="checkbox" id="subregion_structure_brainstem">
             <input type="radio" name="subregion_mode" id="subregion_mode_cross" checked>
             <input type="radio" name="subregion_mode" id="subregion_mode_longitudinal">
             <input id="subregion_sessions" value="1, 2">`
        );

        const result = syncActivePipelineFromForm();

        expect(result.app.subregion_segmentation).toEqual({
            enabled: true,
            structures: ['thalamus'],
            mode: 'cross',
            sessions: ['1', '2'],
        });
    });
});
