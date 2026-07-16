/**
 * Readiness panel: a small, self-contained, reusable step-tracker widget.
 *
 * Deliberately a separate <script src="..."> file, not appended to the
 * existing inline <script> block in templates/index.html -- new, additive
 * code only, so it carries zero risk to the ~7000 lines of already-working
 * logic there. It has no dependency on that script's globals; the host page
 * hands it everything it needs via `getParams`/`onReady` callbacks.
 *
 * Renders the {ready, checks: [{id, label, ok, blocking, detail}]} shape
 * returned by GET /local_run_readiness and GET /cohort/readiness, generalizing
 * the {checks, ready, blockingFailures} convention the existing
 * evaluate*Preflight() functions already use client-side -- this is the
 * server-verified counterpart, checking things a browser can't (does the
 * container file actually exist on disk, is the execution_adapter going to
 * resolve to what you expect, is DataLad SSH reachable, etc.).
 *
 * Usage:
 *   const panel = initReadinessPanel({
 *     containerId: 'localReadinessPanel',
 *     apiUrl: '/local_run_readiness',
 *     getParams: () => ({ project_id: lastProjectId, pipeline_id: currentPipelineId }),
 *     onReady: (ready) => { submitBtn.disabled = !ready; },
 *   });
 *   panel.refresh();               // call after save/project-load/etc.
 *   panel.startPolling(15000);     // optional: keep it fresh on its own
 */
(function (global) {
  'use strict';

  const ICONS = {
    ok: '<i class="fas fa-check-circle" style="color:#1f8b5c;"></i>',
    blocking: '<i class="fas fa-times-circle" style="color:#c0392b;"></i>',
    warning: '<i class="fas fa-info-circle" style="color:#6c757d;"></i>',
  };

  function _iconFor(check) {
    if (check.ok) return ICONS.ok;
    return check.blocking ? ICONS.blocking : ICONS.warning;
  }

  function _escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = String(value == null ? '' : value);
    return div.innerHTML;
  }

  function _render(container, state) {
    if (state.loading) {
      container.innerHTML = '<div class="text-muted small"><i class="fas fa-spinner fa-spin me-1"></i>Checking readiness…</div>';
      return;
    }
    if (state.error) {
      container.innerHTML = `<div class="alert alert-warning py-2 px-3 mb-0 small">Could not check readiness: ${_escapeHtml(state.error)}</div>`;
      return;
    }

    const checks = Array.isArray(state.checks) ? state.checks : [];
    if (!checks.length) {
      container.innerHTML = '<div class="text-muted small">Nothing to check yet.</div>';
      return;
    }

    const items = checks
      .map((check) => {
        const detail = check.detail ? `<div class="small text-muted" style="margin-left:22px;">${_escapeHtml(check.detail)}</div>` : '';
        return `<div class="py-1">
          <div>${_iconFor(check)} <span style="font-weight:${check.ok ? '400' : '600'};">${_escapeHtml(check.label)}</span></div>
          ${detail}
        </div>`;
      })
      .join('');

    const badge = state.ready
      ? '<span class="badge bg-success">Ready</span>'
      : '<span class="badge bg-secondary">Not ready</span>';

    container.innerHTML = `
      <div class="d-flex justify-content-between align-items-center mb-2">
        <span class="fw-semibold small">Readiness</span>
        ${badge}
      </div>
      ${items}
    `;
  }

  function initReadinessPanel({ containerId, apiUrl, getParams, onReady }) {
    const container = document.getElementById(containerId);
    if (!container) {
      console.warn(`readiness_panel.js: no element with id "${containerId}"`);
      return { refresh: async () => {}, startPolling: () => {}, stopPolling: () => {} };
    }

    let pollTimer = null;
    let generation = 0;

    async function refresh() {
      const myGeneration = ++generation;
      _render(container, { loading: true });

      let params = {};
      try {
        params = (typeof getParams === 'function' ? getParams() : {}) || {};
      } catch (e) {
        console.error('readiness_panel.js: getParams() threw', e);
      }

      const query = new URLSearchParams(
        Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''))
      ).toString();

      try {
        const resp = await fetch(`${apiUrl}${query ? '?' + query : ''}`);
        const data = await resp.json();
        if (myGeneration !== generation) return; // a newer refresh() superseded this one
        if (!resp.ok) {
          _render(container, { error: data.error || `HTTP ${resp.status}` });
          if (typeof onReady === 'function') onReady(false);
          return;
        }
        _render(container, { ready: !!data.ready, checks: data.checks || [] });
        if (typeof onReady === 'function') onReady(!!data.ready);
      } catch (e) {
        if (myGeneration !== generation) return;
        _render(container, { error: e.message || String(e) });
        if (typeof onReady === 'function') onReady(false);
      }
    }

    function startPolling(intervalMs) {
      stopPolling();
      pollTimer = setInterval(refresh, intervalMs || 15000);
    }

    function stopPolling() {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    return { refresh, startPolling, stopPolling };
  }

  global.initReadinessPanel = initReadinessPanel;
})(window);
