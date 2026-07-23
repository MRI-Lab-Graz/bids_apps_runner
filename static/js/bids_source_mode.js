/**
 * BIDS Dataset Source toggle (Local Folder vs Remote (SSH)) for the Run App
 * page's "Data Locations" section.
 *
 * Extracted from the inline <script> in templates/index.html -- same pattern
 * as static/js/project_loader.js: a plain classic <script src="...">, loaded
 * before the inline <script>, sharing the same global (window) scope on
 * purpose. Function declarations are global regardless of which script tag
 * defines them; the state these functions read/write (_bidsSourceMode,
 * _remoteStudiesLoaded) is declared with `var` rather than `let` in the
 * inline script specifically so it's visible here too.
 *
 * Local and Remote are meant to be mutually exclusive data sources. Before
 * this fix, switching the toggle only changed button styling and row
 * visibility -- it never reset bids_folder / pipeline_output_root, so a
 * derivatives root left over from a previous Local session kept silently
 * feeding the Output Folder preview even after switching to Remote (or vice
 * versa), producing a "mixed" path made of stale values from both modes.
 * Only an explicit user click drops the other mode's fields now; the
 * one-time mode inference run at project-load time (see
 * inferBidsSourceMode's call site) must NOT trigger this, since that call is
 * just syncing the toggle to the bids_folder the project was just loaded
 * with, not the user choosing to abandon it.
 */

var REMOTE_DATASET_ROOT_PREFIX = '/cl_tmp/mrilab/';

// Best-effort default: a bids_folder already under the lab's local clone
// root means this project's data came from the remote source; otherwise
// assume a plain local folder. There's no separate persisted "mode" field.
function inferBidsSourceMode(bidsFolder) {
    return (bidsFolder || '').startsWith(REMOTE_DATASET_ROOT_PREFIX) ? 'remote' : 'local';
}

function switchBidsSourceMode(mode, userInitiated) {
    _bidsSourceMode = mode === 'remote' ? 'remote' : 'local';
    const isRemote = _bidsSourceMode === 'remote';

    const localBtn = document.getElementById('bidsSourceLocalBtn');
    const remoteBtn = document.getElementById('bidsSourceRemoteBtn');
    const remoteRow = document.getElementById('remoteDatasetRow');
    const bidsFolderCol = document.getElementById('bidsFolderCol');

    localBtn.className = isRemote ? 'btn btn-outline-primary btn-sm px-3' : 'btn btn-primary btn-sm px-3';
    remoteBtn.className = isRemote ? 'btn btn-primary btn-sm px-3' : 'btn btn-outline-primary btn-sm px-3';
    remoteRow.style.display = isRemote ? '' : 'none';
    bidsFolderCol.style.display = isRemote ? 'none' : '';

    if (userInitiated) {
        const bidsFolderEl = document.getElementById('bids_folder');
        const outputRootEl = document.getElementById('pipeline_output_root');
        const outputFolderEl = document.getElementById('output_folder');
        const remoteSelectEl = document.getElementById('remote_dataset_study');

        // Drop whichever mode's fields we're leaving so the two sources can
        // never mix: a bids_folder that doesn't belong to the newly-active
        // mode, and the derivatives root/output folder derived from it.
        if (bidsFolderEl && inferBidsSourceMode(bidsFolderEl.value) !== _bidsSourceMode) {
            bidsFolderEl.value = '';
        }
        if (!isRemote && remoteSelectEl) {
            remoteSelectEl.value = '';
        }
        if (outputRootEl) {
            outputRootEl.value = '';
            delete outputRootEl.dataset.remoteAutoFilled;
        }
        if (outputFolderEl) {
            outputFolderEl.value = '';
        }
        if (typeof updateCohortDatasetDisplay === 'function') {
            updateCohortDatasetDisplay();
        }
        if (typeof applyVersionedOutputPaths === 'function') {
            applyVersionedOutputPaths({ source: 'bids-source-mode-switch' });
        }
    }

    if (isRemote && !_remoteStudiesLoaded && typeof loadRemoteStudies === 'function') {
        loadRemoteStudies();
    }
    if (isRemote && typeof triggerDataladSSHCheck === 'function') {
        triggerDataladSSHCheck();
    }
}
