import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript } from './helpers/loadScript.js';

beforeEach(() => {
    document.body.innerHTML = '';
    loadScript('project_loader.js');
});

describe('buildContainerOptionEntries', () => {
    it('labels remote-only containers as not downloaded', () => {
        const entries = buildContainerOptionEntries([
            { name: 'mriqc/mriqc_24.0.2.sif', local: true },
            { name: 'fmriprep/fmriprep_24.1.1.sif', local: false },
        ]);

        expect(entries).toEqual([
            { value: 'mriqc/mriqc_24.0.2.sif', label: 'mriqc/mriqc_24.0.2.sif', local: true },
            {
                value: 'fmriprep/fmriprep_24.1.1.sif',
                label: 'fmriprep/fmriprep_24.1.1.sif (not downloaded)',
                local: false,
            },
        ]);
    });

    it('returns an empty list for an empty catalog', () => {
        expect(buildContainerOptionEntries([])).toEqual([]);
    });
});

describe('updateContainerDownloadBtnVisibility', () => {
    beforeEach(() => {
        document.body.innerHTML = `
            <select id="container_select"></select>
            <button id="containerDownloadBtn" style="display:none;"></button>
        `;
    });

    it('shows the download button for a remote-only selection', () => {
        const s = document.getElementById('container_select');
        const opt = new Option('fmriprep/fmriprep_24.1.1.sif (not downloaded)', 'fmriprep/fmriprep_24.1.1.sif');
        opt.dataset.local = '0';
        s.add(opt);

        updateContainerDownloadBtnVisibility();

        expect(document.getElementById('containerDownloadBtn').style.display).toBe('block');
    });

    it('hides the download button for a local selection', () => {
        const s = document.getElementById('container_select');
        const opt = new Option('mriqc/mriqc_24.0.2.sif', 'mriqc/mriqc_24.0.2.sif');
        opt.dataset.local = '1';
        s.add(opt);

        updateContainerDownloadBtnVisibility();

        expect(document.getElementById('containerDownloadBtn').style.display).toBe('none');
    });

    it('hides the download button when nothing is selected', () => {
        updateContainerDownloadBtnVisibility();

        expect(document.getElementById('containerDownloadBtn').style.display).toBe('none');
    });
});

describe('Load Options button for a not-downloaded container', () => {
    beforeEach(() => {
        document.body.innerHTML = `
            <select id="container_engine"><option value="apptainer" selected></option></select>
            <input id="container_folder" value="/c">
            <select id="container_select"></select>
            <button id="fetchHelpBtn" style="display:none;"></button>
        `;
    });

    function selectOption(local) {
        const opt = new Option('x', 'qsiprep/qsiprep_1.1.1.sif');
        opt.dataset.local = local;
        document.getElementById('container_select').add(opt);
    }

    it('is hidden -- there is no .sif to run --help on yet', () => {
        selectOption('0');
        _updateFetchHelpBtnOnly();
        expect(document.getElementById('fetchHelpBtn').style.display).toBe('none');
    });

    it('is shown once the container is local', () => {
        selectOption('1');
        _updateFetchHelpBtnOnly();
        expect(document.getElementById('fetchHelpBtn').style.display).toBe('block');
    });
});

describe('containerFetchProgress', () => {
    it('reads the last percentage from rsync --info=progress2 output', () => {
        const tail = '    104,857,600   1%   50.00MB/s    0:03:20\n  4,718,592,000  45%   48.10MB/s    0:01:40';
        expect(containerFetchProgress(tail)).toBe('45%');
    });

    it('returns an empty string before rsync reports anything', () => {
        expect(containerFetchProgress('')).toBe('');
        expect(containerFetchProgress(undefined)).toBe('');
    });
});

describe('downloadSelectedContainer', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        document.body.innerHTML = `
            <input id="container_folder" value="/c">
            <select id="container_select"></select>
            <button id="containerDownloadBtn">Download</button>
        `;
        const opt = new Option('x', 'qsiprep/qsiprep_1.1.1.sif');
        opt.dataset.local = '0';
        document.getElementById('container_select').add(opt);
        window.showStatus = vi.fn();
        window.updateFetchHelpBtnVisibility = vi.fn();
        window.scanContainers = vi.fn(async () => true);
        window.fetchAppOptions = vi.fn(async () => {});
    });

    afterEach(() => vi.useRealTimers());

    it('shows progress on the button, then loads options when the download is ready', async () => {
        const replies = [
            { job_id: 'j1' },
            { status: 'running', log_tail: '  1,000  45%  1MB/s  0:01:00', error: '' },
            { status: 'completed', log_tail: '', error: '' },
        ];
        window.fetch = vi.fn(async () => ({ ok: true, json: async () => replies.shift() }));
        const btn = document.getElementById('containerDownloadBtn');

        const done = downloadSelectedContainer();
        await vi.advanceTimersByTimeAsync(2000);
        expect(btn.disabled).toBe(true);
        expect(btn.textContent).toContain('45%');

        await vi.advanceTimersByTimeAsync(2000);
        await done;

        expect(window.scanContainers).toHaveBeenCalled();
        expect(window.fetchAppOptions).toHaveBeenCalled();
        expect(btn.disabled).toBe(false);
        expect(btn.textContent).toBe('Download');
        expect(window.showStatus).toHaveBeenLastCalledWith(expect.stringContaining('ready'));
    });
});

describe('containerAppName', () => {
    it.each([
        ['qsiprep/qsiprep_26.0.0.sif', 'qsiprep'],
        ['qsiprep/qsiprep-1.0.0.sif', 'qsiprep'],
        ['fastsurfer/fastsurfer_bids_cuda-v2.5.4.sif', 'fastsurfer_bids_cuda'],
        ['freesurfer/freesurfer_bids_8.2.0.sif', 'freesurfer_bids'],
        ['freesurfer/freesurfer_8.2.0.sif', 'freesurfer'],
        ['rshrf/rshrf_v1.7.0.sif', 'rshrf'],
        ['_code/bidspm_4.0.0.sif', 'bidspm'],
        ['custom.simg', 'custom'],
    ])('%s -> %s', (rel, app) => {
        expect(containerAppName(rel)).toBe(app);
    });
});

describe('app -> version container selects', () => {
    const catalog = [
        { name: 'fmriprep/fmriprep_25.2.5.sif', local: true },
        { name: 'qsiprep/qsiprep_1.1.1.sif', local: false },
        { name: 'qsiprep/qsiprep_26.0.0.sif', local: true },
        { name: 'qsiprep/qsiprep_0.24.0.sif', local: false },
    ];

    beforeEach(() => {
        document.body.innerHTML = `
            <input id="container_folder" value="/root">
            <select id="container_app_select"></select>
            <select id="container_select"></select>
        `;
    });

    const apps = () => Array.from(document.getElementById('container_app_select').options).map((o) => o.value);
    const versions = () =>
        Array.from(document.getElementById('container_select').options).map((o) => [o.value, o.text]);

    it('lists each app once and only that app\'s versions, newest first', () => {
        populateContainerSelects(catalog);
        selectContainer('qsiprep/qsiprep_1.1.1.sif');

        expect(apps()).toEqual(['fmriprep', 'qsiprep']);
        expect(versions()).toEqual([
            ['qsiprep/qsiprep_26.0.0.sif', 'qsiprep_26.0.0.sif'],
            ['qsiprep/qsiprep_1.1.1.sif', 'qsiprep_1.1.1.sif (not downloaded)'],
            ['qsiprep/qsiprep_0.24.0.sif', 'qsiprep_0.24.0.sif (not downloaded)'],
        ]);
        expect(document.getElementById('container_select').value).toBe('qsiprep/qsiprep_1.1.1.sif');
        expect(document.getElementById('container_select').selectedOptions[0].dataset.local).toBe('0');
    });

    it('defaults to the first app\'s newest version', () => {
        populateContainerSelects(catalog);

        expect(document.getElementById('container_app_select').value).toBe('fmriprep');
        expect(document.getElementById('container_select').value).toBe('fmriprep/fmriprep_25.2.5.sif');
    });

    it('keeps the previous selection across a rescan', () => {
        populateContainerSelects(catalog);
        selectContainer('qsiprep/qsiprep_0.24.0.sif');

        populateContainerSelects(catalog);

        expect(document.getElementById('container_select').value).toBe('qsiprep/qsiprep_0.24.0.sif');
    });

    it('switching the app shows its newest version and fires change on the version select', () => {
        populateContainerSelects(catalog);
        const changed = vi.fn();
        document.getElementById('container_select').addEventListener('change', changed);

        const appSel = document.getElementById('container_app_select');
        appSel.value = 'qsiprep';
        onContainerAppChange();

        expect(document.getElementById('container_select').value).toBe('qsiprep/qsiprep_26.0.0.sif');
        expect(changed).toHaveBeenCalledTimes(1);
    });

    it('selectContainer accepts a saved absolute path or a bare filename', () => {
        populateContainerSelects(catalog);

        expect(selectContainer('/root/qsiprep/qsiprep_26.0.0.sif')).toBe(true);
        expect(document.getElementById('container_app_select').value).toBe('qsiprep');
        expect(document.getElementById('container_select').value).toBe('qsiprep/qsiprep_26.0.0.sif');

        expect(selectContainer('fmriprep_25.2.5.sif')).toBe(true);
        expect(document.getElementById('container_select').value).toBe('fmriprep/fmriprep_25.2.5.sif');

        expect(selectContainer('mriqc_24.0.2.sif')).toBe(false);
    });
});
