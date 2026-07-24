// Minimal stand-in for the real "5. Execution Parameters" section of
// templates/index.html -- just the element ids/classes
// syncActivePipelineFromForm() (pipeline_form_sync.js) actually reads.
export function executionParamsFixtureHtml() {
    return `
        <input id="bids_folder" value="/data/bids">
        <input id="output_folder" value="/data/derivatives/app">
        <input id="tmp_folder" value="/scratch/tmp">
        <input id="templateflow_dir" value="">
        <input id="fs_license_file" value="">
        <input id="pipeline_output_root" value="/data/derivatives">
        <input id="pipeline_version" value="">
        <input type="checkbox" id="pipeline_auto_versioning">
        <input id="pipeline_app_name" value="">
        <input id="notify_email" value="">
        <input id="jobs" value="1">
        <select id="analysis_level"><option value="participant" selected>participant</option></select>

        <input type="checkbox" id="gpu_enabled">

        <select id="container_engine">
            <option value="apptainer" selected>apptainer</option>
            <option value="docker">docker</option>
        </select>
        <input id="container_folder" value="/containers">
        <select id="container_select"><option value="app.sif" selected>app.sif</option></select>
        <input id="container_docker_repo" value="">
        <select id="container_docker_tag"><option value="latest" selected>latest</option></select>

        <textarea id="custom_args"></textarea>
        <textarea id="custom_mounts"></textarea>

        <input type="checkbox" id="fastsurfer_longitudinal">
        <input type="checkbox" id="freesurfer_bids_longitudinal">

        <select id="qsirecon_recon_spec"><option value="">-- choose --</option><option value="mrtrix_multishell_msmt">mrtrix_multishell_msmt</option></select>
        <input id="qsirecon_fs_subjects_dir" value="">
    `;
}
