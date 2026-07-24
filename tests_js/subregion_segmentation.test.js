import { describe, it, expect, beforeEach } from 'vitest';
import { loadScript } from './helpers/loadScript.js';

const FIXTURE_HTML = `
    <div id="subregion_segmentation_section_wrap">
        <input type="checkbox" id="subregion_segmentation_enabled">
        <div id="subregion_segmentation_options"></div>
        <input type="checkbox" id="subregion_structure_thalamus">
        <input type="checkbox" id="subregion_structure_hippo_amygdala">
        <input type="checkbox" id="subregion_structure_brainstem">
        <input type="radio" name="subregion_mode" id="subregion_mode_cross">
        <input type="radio" name="subregion_mode" id="subregion_mode_longitudinal">
        <input id="subregion_sessions" value="">
    </div>
    <button id="cohortSubmitSubregionsBtn"></button>
`;

beforeEach(() => {
    document.body.innerHTML = FIXTURE_HTML;
    loadScript('subregion_segmentation.js');
});

describe('toggleSubregionSegmentationOptions', () => {
    it('shows the options panel only when enabled is checked', () => {
        const enabled = document.getElementById('subregion_segmentation_enabled');
        const options = document.getElementById('subregion_segmentation_options');

        enabled.checked = true;
        toggleSubregionSegmentationOptions();
        expect(options.style.display).toBe('');

        enabled.checked = false;
        toggleSubregionSegmentationOptions();
        expect(options.style.display).toBe('none');
    });
});

describe('updateSubregionSegmentationVisibility', () => {
    it('shows the section and cohort button only for the freesurfer pipeline', () => {
        window.inferCurrentPipelineAppName = () => 'freesurfer';
        updateSubregionSegmentationVisibility();
        expect(document.getElementById('subregion_segmentation_section_wrap').style.display).toBe('');
        expect(document.getElementById('cohortSubmitSubregionsBtn').style.display).toBe('');

        window.inferCurrentPipelineAppName = () => 'qsiprep';
        updateSubregionSegmentationVisibility();
        expect(document.getElementById('subregion_segmentation_section_wrap').style.display).toBe('none');
        expect(document.getElementById('cohortSubmitSubregionsBtn').style.display).toBe('none');
    });

    it('defaults to hidden when inferCurrentPipelineAppName is unavailable', () => {
        delete window.inferCurrentPipelineAppName;
        updateSubregionSegmentationVisibility();
        expect(document.getElementById('subregion_segmentation_section_wrap').style.display).toBe('none');
    });
});

describe('restoreSubregionSegmentationUI / collectSubregionSegmentationConfig round-trip', () => {
    it('restores a saved config into the form exactly', () => {
        const saved = {
            enabled: true,
            structures: ['hippo-amygdala', 'brainstem'],
            mode: 'longitudinal',
            sessions: ['1', '2'],
        };

        restoreSubregionSegmentationUI({ subregion_segmentation: saved });

        expect(document.getElementById('subregion_segmentation_enabled').checked).toBe(true);
        expect(document.getElementById('subregion_structure_thalamus').checked).toBe(false);
        expect(document.getElementById('subregion_structure_hippo_amygdala').checked).toBe(true);
        expect(document.getElementById('subregion_structure_brainstem').checked).toBe(true);
        expect(document.getElementById('subregion_mode_longitudinal').checked).toBe(true);
        expect(document.getElementById('subregion_sessions').value).toBe('1,2');
    });

    it('collects the form back into the same shape it restores from', () => {
        const saved = {
            enabled: true,
            structures: ['thalamus', 'brainstem'],
            mode: 'cross',
            sessions: ['3'],
        };
        restoreSubregionSegmentationUI({ subregion_segmentation: saved });

        expect(collectSubregionSegmentationConfig()).toEqual(saved);
    });

    it('defaults to disabled/cross/no-structures when app has no subregion_segmentation config', () => {
        restoreSubregionSegmentationUI({});

        expect(collectSubregionSegmentationConfig()).toEqual({
            enabled: false,
            structures: [],
            mode: 'cross',
            sessions: [],
        });
    });

    it('trims whitespace and drops empty entries from the sessions field', () => {
        document.getElementById('subregion_sessions').value = ' 1 ,, 2 ,3';
        expect(collectSubregionSegmentationConfig().sessions).toEqual(['1', '2', '3']);
    });

    it('collectSubregionSegmentationConfig returns null when the form is not present', () => {
        document.body.innerHTML = '';
        expect(collectSubregionSegmentationConfig()).toBeNull();
    });
});
