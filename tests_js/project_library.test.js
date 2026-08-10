import { describe, it, expect, beforeEach, vi } from 'vitest';
import { loadScript } from './helpers/loadScript.js';

const FIXTURE_HTML = `
    <div id="recentProjectsMeta"></div>
    <div id="recentProjectsList"></div>
    <button id="showAllProjectsBtn" style="display:none;"></button>
`;

beforeEach(() => {
    document.body.innerHTML = FIXTURE_HTML;
    window.lastProjectId = '';
    window.RECENT_PROJECTS_LIMIT = 5;
    window.ensureProjectPipelineState = vi.fn(() => ({
        activePipeline: 'default',
        pipelines: { default: { name: 'Default', common: {}, app: {} } },
    }));
    window.escapeHtml = value => String(value);
    window._formatDateTimeForDisplay = value => value;
    window.loadProjectAndSwitch = vi.fn();
    window.deleteProject = vi.fn();
    loadScript('project_loader.js');
});

describe('loadRecentProjects', () => {
    it('requests and renders every persisted project when Show all is selected', async () => {
        window.fetch = vi.fn().mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({
                limit: 'all',
                total_projects: 6,
                projects: Array.from({ length: 6 }, (_, index) => ({
                    id: `study_${index + 1}`,
                    name: `Study ${index + 1}`,
                    description: '',
                    last_modified: '2026-08-08T00:00:00',
                    config: {},
                })),
            }),
        });

        await loadRecentProjects(true);

        expect(window.fetch).toHaveBeenCalledWith('/get_projects?limit=all');
        expect(document.querySelectorAll('.project-load-btn')).toHaveLength(6);
        expect(document.getElementById('recentProjectsMeta').textContent).toContain('Showing 6 of 6');
        expect(document.getElementById('showAllProjectsBtn').style.display).toBe('none');
    });
});