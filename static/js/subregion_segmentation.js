// FreeSurfer subregion segmentation (thalamus / hippo-amygdala / brainstem,
// https://surfer.nmr.mgh.harvard.edu/fswiki/SubregionSegmentation) options.
// Cohort/SLURM-array-only follow-up step (see submit_bids_cohort.sh ::
// submit_subregion_segmentation) -- stored as app.subregion_segmentation
// alongside app.execution_adapter, the same way the freesurfer_bids_longitudinal
// checkbox stores its choice, so it round-trips through Save/Load with the
// rest of the pipeline's "app" config.

function toggleSubregionSegmentationOptions() {
    const enabled = document.getElementById('subregion_segmentation_enabled');
    const options = document.getElementById('subregion_segmentation_options');
    if (!enabled || !options) return;
    options.style.display = enabled.checked ? '' : 'none';
}

// Unlike most pipeline-specific fields in this form (which stay visible for
// every pipeline, labeled "X only" in a tooltip), this section and its
// Cohort-panel button actually DO something wrong if used against a
// non-FreeSurfer pipeline (segment_subregions has nothing to run against a
// QSIPrep/etc. output tree) -- confirmed as a real point of confusion: the
// "Submit Subregions Only" button stayed visible after switching the active
// pipeline to qsiprep. Hide both outright rather than relying on a tooltip.
function updateSubregionSegmentationVisibility() {
    const isFreesurfer = typeof inferCurrentPipelineAppName === 'function'
        && inferCurrentPipelineAppName() === 'freesurfer';

    const sectionWrap = document.getElementById('subregion_segmentation_section_wrap');
    if (sectionWrap) sectionWrap.style.display = isFreesurfer ? '' : 'none';

    const cohortBtn = document.getElementById('cohortSubmitSubregionsBtn');
    if (cohortBtn) cohortBtn.style.display = isFreesurfer ? '' : 'none';
}

// Populate the UI from a loaded pipeline's app config.
function restoreSubregionSegmentationUI(app) {
    const cfg = (app && app.subregion_segmentation) || {};
    const enabledEl = document.getElementById('subregion_segmentation_enabled');
    if (enabledEl) enabledEl.checked = !!cfg.enabled;

    const structures = new Set(cfg.structures || []);
    const thalamusEl = document.getElementById('subregion_structure_thalamus');
    const hippoAmygdalaEl = document.getElementById('subregion_structure_hippo_amygdala');
    const brainstemEl = document.getElementById('subregion_structure_brainstem');
    if (thalamusEl) thalamusEl.checked = structures.has('thalamus');
    if (hippoAmygdalaEl) hippoAmygdalaEl.checked = structures.has('hippo-amygdala');
    if (brainstemEl) brainstemEl.checked = structures.has('brainstem');

    const mode = cfg.mode === 'longitudinal' ? 'longitudinal' : 'cross';
    const modeEl = document.getElementById('subregion_mode_' + mode);
    if (modeEl) modeEl.checked = true;

    const sessionsEl = document.getElementById('subregion_sessions');
    if (sessionsEl) sessionsEl.value = (cfg.sessions || []).join(',');

    toggleSubregionSegmentationOptions();
    updateSubregionSegmentationVisibility();
}

// Read the UI state back into the shape stored on app.subregion_segmentation.
function collectSubregionSegmentationConfig() {
    const enabledEl = document.getElementById('subregion_segmentation_enabled');
    if (!enabledEl) return null;

    const structures = [];
    if (document.getElementById('subregion_structure_thalamus')?.checked) structures.push('thalamus');
    if (document.getElementById('subregion_structure_hippo_amygdala')?.checked) structures.push('hippo-amygdala');
    if (document.getElementById('subregion_structure_brainstem')?.checked) structures.push('brainstem');

    const mode = document.getElementById('subregion_mode_longitudinal')?.checked ? 'longitudinal' : 'cross';

    const sessions = (document.getElementById('subregion_sessions')?.value || '')
        .split(',')
        .map(s => s.trim())
        .filter(Boolean);

    return {
        enabled: !!enabledEl.checked,
        structures: structures,
        mode: mode,
        sessions: sessions,
    };
}
