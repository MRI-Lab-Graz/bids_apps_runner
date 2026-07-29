import { describe, it, expect, beforeEach } from 'vitest';
import { loadScript } from './helpers/loadScript.js';

beforeEach(() => {
    document.body.innerHTML = '';
    loadScript('missing_items.js');
});

describe('summarizeMissingItem', () => {
    it('disambiguates multi-line QSIPrep-style entries by Subject/Session', () => {
        const ses2 = '[ERROR] DWI directory missing for session with DWI data in other sessions:\n'
            + '    Subject:  sub-01\n'
            + '    Session:  ses-2\n'
            + '    Expected: /derivatives/qsiprep/sub-01/ses-2/dwi\n'
            + '    Note:     Other sessions have DWI data, QSIPrep output expected';
        const ses3 = '[ERROR] DWI directory missing for session with DWI data in other sessions:\n'
            + '    Subject:  sub-01\n'
            + '    Session:  ses-3\n'
            + '    Expected: /derivatives/qsiprep/sub-01/ses-3/dwi\n'
            + '    Note:     Other sessions have DWI data, QSIPrep output expected';

        const summary2 = summarizeMissingItem(ses2);
        const summary3 = summarizeMissingItem(ses3);

        expect(summary2).toContain('sub-01');
        expect(summary2).toContain('ses-2');
        expect(summary3).toContain('ses-3');
        expect(summary2).not.toBe(summary3);
    });

    it('falls back to a sub-XX/ses-YY regex for single-line checker messages', () => {
        const item = '[ERROR] Missing preprocessed output for sub-02 ses-1: expected file not found';
        expect(summarizeMissingItem(item)).toBe(
            'Missing preprocessed output for sub-02 ses-1: expected file not found (sub-02, ses-1)'
        );
    });

    it('returns just the headline for global issues with no subject/session', () => {
        const item = '[ERROR] Pipeline directory not found: /derivatives/qsiprep';
        expect(summarizeMissingItem(item)).toBe('Pipeline directory not found: /derivatives/qsiprep');
    });

    it('strips the severity prefix', () => {
        expect(summarizeMissingItem('[WARNING] Some issue')).toBe('Some issue');
    });
});
