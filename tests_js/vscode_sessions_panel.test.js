import { describe, it, expect, beforeEach, vi } from 'vitest';
import { loadScript } from './helpers/loadScript.js';

const FIXTURE_HTML = `
    <div id="vscodeSessionsPanel" style="display:none;">
        <span id="vscodeSessionsBadge"></span>
        <div id="vscodeSessionsDetail"></div>
        <button id="vscodeSessionsCleanupBtn" style="display:none;"></button>
    </div>
`;

beforeEach(() => {
    document.body.innerHTML = FIXTURE_HTML;
    loadScript('vscode_sessions_panel.js');
});

describe('refreshVscodeSessionsPanel', () => {
    it('stays hidden when there are no stale stacks', async () => {
        window.fetch = vi.fn().mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({
                stacks: [{ hash: 'aaaa1111', pids: [1], age_seconds: 60, rss_bytes: 500, stale: false }],
                total_count: 1,
                stale_count: 0,
                stale_rss_bytes: 0,
            }),
        });

        await refreshVscodeSessionsPanel();

        expect(document.getElementById('vscodeSessionsPanel').style.display).toBe('none');
        expect(document.getElementById('vscodeSessionsCleanupBtn').style.display).toBe('none');
    });

    it('shows the banner, badge count, and cleanup button when stale stacks exist', async () => {
        window.fetch = vi.fn().mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({
                stacks: [
                    { hash: 'aaaa1111aaaa2222', pids: [1, 2], age_seconds: 20 * 3600, rss_bytes: 3 * 1024 * 1024, stale: true },
                    { hash: 'bbbb1111bbbb2222', pids: [3], age_seconds: 60, rss_bytes: 500 * 1024, stale: false },
                ],
                total_count: 2,
                stale_count: 1,
                stale_rss_bytes: 3 * 1024 * 1024,
            }),
        });

        await refreshVscodeSessionsPanel();

        const panel = document.getElementById('vscodeSessionsPanel');
        expect(panel.style.display).toBe('block');
        expect(document.getElementById('vscodeSessionsBadge').textContent).toBe('1 stale');
        expect(document.getElementById('vscodeSessionsCleanupBtn').style.display).toBe('inline-flex');
        const detail = document.getElementById('vscodeSessionsDetail').innerHTML;
        expect(detail).toContain('aaaa1111aaaa'); // truncated hash
        expect(detail).not.toContain('bbbb1111'); // only the stale one is listed
    });

    it('hides the panel and does not throw when the check itself fails', async () => {
        window.fetch = vi.fn().mockRejectedValue(new Error('network down'));

        await expect(refreshVscodeSessionsPanel()).resolves.toBeUndefined();

        expect(document.getElementById('vscodeSessionsPanel').style.display).toBe('none');
    });
});

describe('cleanupVscodeSessionsPanel', () => {
    async function primeWithOneStaleStack() {
        window.fetch = vi.fn().mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({
                stacks: [{ hash: 'aaaa1111aaaa2222', pids: [1, 2], age_seconds: 20 * 3600, rss_bytes: 100, stale: true }],
                total_count: 1,
                stale_count: 1,
                stale_rss_bytes: 100,
            }),
        });
        await refreshVscodeSessionsPanel();
    }

    it('does not call fetch when the user cancels the confirmation', async () => {
        await primeWithOneStaleStack();
        window.fetch = vi.fn();
        window.confirm = vi.fn().mockReturnValue(false);

        await cleanupVscodeSessionsPanel();

        expect(window.fetch).not.toHaveBeenCalled();
    });

    it('posts to /cleanup_vscode_session for the stale stack and re-checks afterward when confirmed', async () => {
        await primeWithOneStaleStack();
        window.confirm = vi.fn().mockReturnValue(true);
        const postCalls = [];
        window.fetch = vi.fn((url, options) => {
            if (options && options.method === 'POST') {
                postCalls.push({ url, body: JSON.parse(options.body) });
                return Promise.resolve({ ok: true, json: () => Promise.resolve({ message: 'ok', pid_count: 2 }) });
            }
            // the re-check GET fired in the `finally`-equivalent refresh() call
            return Promise.resolve({
                ok: true,
                json: () => Promise.resolve({ stacks: [], total_count: 0, stale_count: 0, stale_rss_bytes: 0 }),
            });
        });

        await cleanupVscodeSessionsPanel();

        expect(postCalls).toHaveLength(1);
        expect(postCalls[0].url).toBe('/cleanup_vscode_session');
        expect(postCalls[0].body).toEqual({ hash: 'aaaa1111aaaa2222' });
        // re-checked and found clean -> banner hidden again
        expect(document.getElementById('vscodeSessionsPanel').style.display).toBe('none');
    });
});
