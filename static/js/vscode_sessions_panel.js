/**
 * VS Code sessions panel: on page load, warns this OS user if they have
 * stale VS Code Remote-SSH server stacks (>=12h old) still running on this
 * host, with a one-click cleanup action. See CLAUDE.md's HPC login node
 * policy -- a shared login node should have a small footprint, and VS
 * Code's own idle auto-shutdown isn't always reliable in practice (real
 * example: a stack ran 23+ hours despite --enable-remote-auto-shutdown).
 *
 * Backed by GET /check_vscode_sessions and POST /cleanup_vscode_session in
 * gui/gui_system_routes.py, which only ever inspect/touch this OS user's
 * own processes.
 *
 * Same plain classic <script src="...">, global-scope pattern as
 * static/js/cohort_panel.js -- a singleton panel (there's only ever one on
 * the page), so no factory/instance indirection like readiness_panel.js
 * needs for its multiple reusable call sites.
 */

let _vscodeStaleStacks = [];

function _vscodeEscapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = String(value == null ? '' : value);
    return div.innerHTML;
}

function _vscodeFormatAge(seconds) {
    if (seconds == null) return 'unknown age';
    const hours = seconds / 3600;
    if (hours < 1) return `${Math.round(seconds / 60)}m`;
    if (hours < 48) return `${hours.toFixed(1)}h`;
    return `${(hours / 24).toFixed(1)}d`;
}

function _vscodeFormatBytes(bytes) {
    if (!bytes) return '0 MB';
    const mb = bytes / (1024 * 1024);
    if (mb < 1024) return `${mb.toFixed(0)} MB`;
    return `${(mb / 1024).toFixed(1)} GB`;
}

async function refreshVscodeSessionsPanel() {
    const panel = document.getElementById('vscodeSessionsPanel');
    const badge = document.getElementById('vscodeSessionsBadge');
    const detail = document.getElementById('vscodeSessionsDetail');
    const cleanupBtn = document.getElementById('vscodeSessionsCleanupBtn');
    if (!panel) return;

    cleanupBtn.style.display = 'none';

    let data;
    try {
        const resp = await fetch('/check_vscode_sessions');
        data = await resp.json();
        if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    } catch (err) {
        // A nice-to-have hint, not critical status -- fail quiet, don't
        // nag the user with an error banner over a check that itself failed.
        panel.style.display = 'none';
        console.warn('vscode_sessions_panel.js: check failed', err);
        return;
    }

    _vscodeStaleStacks = (data.stacks || []).filter((s) => s.stale);

    if (_vscodeStaleStacks.length === 0) {
        panel.style.display = 'none';
        return;
    }

    panel.style.display = 'block';
    panel.className = 'alert alert-warning py-2 px-3 small mb-3';
    badge.className = 'badge bg-warning text-dark';
    badge.textContent = `${_vscodeStaleStacks.length} stale`;
    const lines = _vscodeStaleStacks.map(
        (s) => `<code>${_vscodeEscapeHtml(s.hash.slice(0, 12))}</code> — running ${_vscodeEscapeHtml(_vscodeFormatAge(s.age_seconds))}, ${_vscodeEscapeHtml(_vscodeFormatBytes(s.rss_bytes))}`
    );
    detail.innerHTML = `You have ${_vscodeStaleStacks.length} old VS Code server session(s) still running on this host `
        + `(~${_vscodeEscapeHtml(_vscodeFormatBytes(data.stale_rss_bytes))} total). `
        + `A shared HPC login node should have a small footprint -- close unused windows, or clean up below.<br>`
        + lines.join('<br>');
    cleanupBtn.style.display = 'inline-flex';
}

async function cleanupVscodeSessionsPanel() {
    if (_vscodeStaleStacks.length === 0) return;
    const summary = _vscodeStaleStacks
        .map((s) => `${s.hash.slice(0, 12)} (${_vscodeFormatAge(s.age_seconds)} old)`)
        .join(', ');
    if (!confirm(`Kill ${_vscodeStaleStacks.length} stale VS Code server session(s): ${summary}?\n\nThis will disconnect any window still connected to them.`)) {
        return;
    }

    const cleanupBtn = document.getElementById('vscodeSessionsCleanupBtn');
    cleanupBtn.disabled = true;
    for (const stack of _vscodeStaleStacks) {
        try {
            const resp = await fetch('/cleanup_vscode_session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hash: stack.hash }),
            });
            const data = await resp.json();
            if (!resp.ok) {
                console.warn(`vscode_sessions_panel.js: cleanup of ${stack.hash} failed`, data.error);
            }
        } catch (err) {
            console.warn(`vscode_sessions_panel.js: cleanup of ${stack.hash} failed`, err);
        }
    }
    cleanupBtn.disabled = false;
    await refreshVscodeSessionsPanel();
}
