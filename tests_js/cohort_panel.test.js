import { describe, it, expect, beforeEach, vi } from 'vitest';
import { loadScript } from './helpers/loadScript.js';

const FIXTURE_HTML = `
    <input id="cohort_max_concurrent" value="50">
    <div id="cohortStoragePanel" style="display:none;">
        <span id="cohortStorageBadge"></span>
        <div id="cohortStorageDetail"></div>
        <div id="cohortStorageForceUnverifiedWrap" style="display:none;">
            <input type="checkbox" id="cohortStorageForceUnverified">
            <label id="cohortStorageForceUnverifiedLabel"></label>
        </div>
        <button id="cohortCleanupStorageBtn" style="display:none;"></button>
    </div>
`;

beforeEach(() => {
    document.body.innerHTML = FIXTURE_HTML;
    window.lastProjectId = 'proj-1';
    window.currentPipelineId = 'default';
    window.logHPC = vi.fn();
    window.escapeHtml = (s) => s;
    loadScript('missing_items.js');
    loadScript('cohort_panel.js');
});

describe('checkCohortStorageSync', () => {
    it('shows "Fully synced & complete" and the cleanup button when can_reclaim is true', async () => {
        window.fetch = vi.fn().mockResolvedValue({
            json: () => Promise.resolve({
                output_cloned: true,
                can_reclaim: true,
                output_sync: { ok: true, uncommitted: 0, unpushed: 0 },
                completeness: { ok: true, supported: true, pipeline: 'qsiprep', missing_items: [] },
            }),
        });

        await checkCohortStorageSync();

        expect(document.getElementById('cohortStorageBadge').textContent).toBe('Fully synced & complete');
        expect(document.getElementById('cohortCleanupStorageBtn').style.display).toBe('inline-flex');
        expect(document.getElementById('cohortStorageForceUnverifiedWrap').style.display).toBe('none');
    });

    it('shows missing items and an overridable checkbox when the pipeline output is incomplete', async () => {
        window.fetch = vi.fn().mockResolvedValue({
            json: () => Promise.resolve({
                output_cloned: true,
                can_reclaim: false,
                output_sync: { ok: true, uncommitted: 0, unpushed: 0 },
                completeness: {
                    ok: false,
                    supported: true,
                    pipeline: 'qsiprep',
                    missing_items: ['[ERROR] DWI directory missing for session with DWI data in other sessions:\n    Subject:  sub-01\n    Session:  ses-2'],
                },
            }),
        });

        await checkCohortStorageSync();

        expect(document.getElementById('cohortStorageBadge').textContent).toBe('Incomplete output');
        expect(document.getElementById('cohortCleanupStorageBtn').style.display).toBe('none');
        expect(document.getElementById('cohortStorageDetail').innerHTML).toContain('sub-01');

        // Overridable: checking the box (operator knows why, e.g. excluded
        // subjects) reveals the cleanup button.
        const forceWrap = document.getElementById('cohortStorageForceUnverifiedWrap');
        expect(forceWrap.style.display).toBe('block');
        expect(document.getElementById('cohortStorageForceUnverifiedLabel').textContent).toContain('incomplete');

        const forceCheckbox = document.getElementById('cohortStorageForceUnverified');
        forceCheckbox.checked = true;
        forceCheckbox.onchange();
        expect(document.getElementById('cohortCleanupStorageBtn').style.display).toBe('inline-flex');
    });

    it('shows the force-unverified checkbox for pipelines with no automated checker, and reveals cleanup when checked', async () => {
        window.fetch = vi.fn().mockResolvedValue({
            json: () => Promise.resolve({
                output_cloned: true,
                can_reclaim: false,
                output_sync: { ok: true, uncommitted: 0, unpushed: 0 },
                completeness: {
                    ok: false,
                    supported: false,
                    pipeline: 'custom_app',
                    missing_items: [],
                    error: "No automated output-completeness checker is available for pipeline 'custom_app'.",
                },
            }),
        });

        await checkCohortStorageSync();

        expect(document.getElementById('cohortStorageBadge').textContent).toBe('Unverified');
        expect(document.getElementById('cohortCleanupStorageBtn').style.display).toBe('none');
        const forceWrap = document.getElementById('cohortStorageForceUnverifiedWrap');
        expect(forceWrap.style.display).toBe('block');

        const forceCheckbox = document.getElementById('cohortStorageForceUnverified');
        forceCheckbox.checked = true;
        forceCheckbox.onchange();
        expect(document.getElementById('cohortCleanupStorageBtn').style.display).toBe('inline-flex');
    });

    it('shows "Not synced" with counts and hides the button when can_reclaim is false', async () => {
        window.fetch = vi.fn().mockResolvedValue({
            json: () => Promise.resolve({
                output_cloned: true,
                can_reclaim: false,
                output_sync: { ok: false, uncommitted: 3, unpushed: 2, error: null },
            }),
        });

        await checkCohortStorageSync();

        expect(document.getElementById('cohortStorageBadge').textContent).toBe('Not synced');
        expect(document.getElementById('cohortCleanupStorageBtn').style.display).toBe('none');
        const detail = document.getElementById('cohortStorageDetail').textContent;
        expect(detail).toContain('3 uncommitted');
        expect(detail).toContain('2 unpushed');
    });

    it('shows "Not cloned" when the output dataset has no clone yet', async () => {
        window.fetch = vi.fn().mockResolvedValue({
            json: () => Promise.resolve({ output_cloned: false, can_reclaim: false }),
        });

        await checkCohortStorageSync();

        expect(document.getElementById('cohortStorageBadge').textContent).toBe('Not cloned');
        expect(document.getElementById('cohortCleanupStorageBtn').style.display).toBe('none');
    });

    it('does nothing when no project is loaded', async () => {
        window.lastProjectId = null;
        window.fetch = vi.fn();

        await checkCohortStorageSync();

        expect(window.fetch).not.toHaveBeenCalled();
    });
});

describe('cleanupCohortLocalStorage', () => {
    it('does not call fetch when the user cancels the confirmation', async () => {
        window.confirm = vi.fn().mockReturnValue(false);
        window.fetch = vi.fn();

        await cleanupCohortLocalStorage();

        expect(window.fetch).not.toHaveBeenCalled();
    });

    it('posts to /cohort/cleanup_local_storage and re-checks sync status when confirmed', async () => {
        window.confirm = vi.fn().mockReturnValue(true);
        const fetchMock = vi.fn()
            .mockResolvedValueOnce({ json: () => Promise.resolve({ ok: true }) })
            .mockResolvedValueOnce({ json: () => Promise.resolve({ output_cloned: true, can_reclaim: true }) });
        window.fetch = fetchMock;

        await cleanupCohortLocalStorage();

        expect(fetchMock).toHaveBeenCalledTimes(2);
        const [url, options] = fetchMock.mock.calls[0];
        expect(url).toBe('/cohort/cleanup_local_storage');
        expect(options.method).toBe('POST');
        expect(JSON.parse(options.body)).toMatchObject({ project_id: 'proj-1' });
        expect(fetchMock.mock.calls[1][0]).toContain('/cohort/check_storage_sync');
        expect(document.getElementById('cohortCleanupStorageBtn').disabled).toBe(false);
    });

    it('sends force_incomplete_output (not force_unverified) when overriding an incomplete-but-known pipeline', async () => {
        window.fetch = vi.fn().mockResolvedValue({
            json: () => Promise.resolve({
                output_cloned: true,
                can_reclaim: false,
                output_sync: { ok: true, uncommitted: 0, unpushed: 0 },
                completeness: { ok: false, supported: true, pipeline: 'qsiprep', missing_items: ['[ERROR] sub-99 excluded'] },
            }),
        });
        await checkCohortStorageSync();
        document.getElementById('cohortStorageForceUnverified').checked = true;

        window.confirm = vi.fn().mockReturnValue(true);
        const fetchMock = vi.fn()
            .mockResolvedValueOnce({ json: () => Promise.resolve({ ok: true }) })
            .mockResolvedValueOnce({ json: () => Promise.resolve({ output_cloned: true, can_reclaim: true }) });
        window.fetch = fetchMock;

        await cleanupCohortLocalStorage();

        const [, options] = fetchMock.mock.calls[0];
        const body = JSON.parse(options.body);
        expect(body.force_incomplete_output).toBe(true);
        expect(body.force_unverified).toBe(false);
        expect(window.confirm.mock.calls[0][0]).toContain('INCOMPLETE');
    });

    it('sends force_unverified (not force_incomplete_output) when overriding an unsupported pipeline', async () => {
        window.fetch = vi.fn().mockResolvedValue({
            json: () => Promise.resolve({
                output_cloned: true,
                can_reclaim: false,
                output_sync: { ok: true, uncommitted: 0, unpushed: 0 },
                completeness: { ok: false, supported: false, pipeline: 'custom_app', missing_items: [], error: 'no checker' },
            }),
        });
        await checkCohortStorageSync();
        document.getElementById('cohortStorageForceUnverified').checked = true;

        window.confirm = vi.fn().mockReturnValue(true);
        const fetchMock = vi.fn()
            .mockResolvedValueOnce({ json: () => Promise.resolve({ ok: true }) })
            .mockResolvedValueOnce({ json: () => Promise.resolve({ output_cloned: true, can_reclaim: true }) });
        window.fetch = fetchMock;

        await cleanupCohortLocalStorage();

        const [, options] = fetchMock.mock.calls[0];
        const body = JSON.parse(options.body);
        expect(body.force_unverified).toBe(true);
        expect(body.force_incomplete_output).toBe(false);
    });
});
