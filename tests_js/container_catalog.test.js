import { describe, it, expect, beforeEach } from 'vitest';
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
