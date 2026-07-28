import { describe, it, expect, beforeEach, vi } from 'vitest';
import { loadScript } from './helpers/loadScript.js';

const FIXTURE_HTML = `
    <input id="cohort_max_concurrent" value="50">
    <div id="cohortStoragePanel" style="display:none;">
        <span id="cohortStorageBadge"></span>
        <div id="cohortStorageDetail"></div>
        <button id="cohortCleanupStorageBtn" style="display:none;"></button>
    </div>
`;

beforeEach(() => {
    document.body.innerHTML = FIXTURE_HTML;
    window.lastProjectId = 'proj-1';
    window.currentPipelineId = 'default';
    window.logHPC = vi.fn();
    window.escapeHtml = (s) => s;
    loadScript('cohort_panel.js');
});

describe('checkCohortStorageSync', () => {
    it('shows "Fully synced" and the cleanup button when can_reclaim is true', async () => {
        window.fetch = vi.fn().mockResolvedValue({
            json: () => Promise.resolve({
                output_cloned: true,
                can_reclaim: true,
                output_sync: { ok: true, uncommitted: 0, unpushed: 0 },
            }),
        });

        await checkCohortStorageSync();

        expect(document.getElementById('cohortStorageBadge').textContent).toBe('Fully synced');
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
});
