import { describe, it, expect, beforeEach } from 'vitest';
import { loadScript } from './helpers/loadScript.js';

// The local clone root is a site setting (window.PRISM_SITE, injected by the
// page), not a constant naming one lab's shared folder.
function load(site) {
    window.PRISM_SITE = site;
    loadScript('bids_source_mode.js');
}

describe('remoteDatasetRoot / remoteCloneDirFor', () => {
    beforeEach(() => { delete window.PRISM_SITE; });

    it('is the configured local clone base with exactly one trailing slash', () => {
        load({ localDatasetBase: '/scratch/alice/datasets' });
        expect(remoteDatasetRoot()).toBe('/scratch/alice/datasets/');
        load({ localDatasetBase: '/scratch/alice/datasets///' });
        expect(remoteDatasetRoot()).toBe('/scratch/alice/datasets/');
    });

    it('builds the clone dir for a study', () => {
        load({ localDatasetBase: '/scratch/alice/datasets' });
        expect(remoteCloneDirFor('ds001')).toBe('/scratch/alice/datasets/ds001');
        expect(remoteCloneDirFor('')).toBe('');
    });

    it('is empty when the page gave no site settings', () => {
        load(undefined);
        expect(remoteDatasetRoot()).toBe('');
        expect(remoteCloneDirFor('ds001')).toBe('');
    });
});

describe('inferBidsSourceMode', () => {
    it('is "remote" only for folders under the configured clone root', () => {
        load({ localDatasetBase: '/scratch/alice/datasets' });
        expect(inferBidsSourceMode('/scratch/alice/datasets/ds001')).toBe('remote');
        expect(inferBidsSourceMode('/scratch/alice/other/ds001')).toBe('local');
        expect(inferBidsSourceMode('/scratch/alice/datasets-extra/ds001')).toBe('local');
        expect(inferBidsSourceMode('')).toBe('local');
    });

    it('never claims "remote" when no clone root is configured (empty prefix matches everything)', () => {
        load(undefined);
        expect(inferBidsSourceMode('/anything/at/all')).toBe('local');
    });

    it('does not treat the old hardwired lab folder specially any more', () => {
        load({ localDatasetBase: '/scratch/alice/datasets' });
        expect(inferBidsSourceMode('/cl_tmp/mrilab/129')).toBe('local');
    });
});
