import { describe, it, expect, beforeEach, vi } from 'vitest';
import { loadScript } from './helpers/loadScript.js';

const FIXTURE_HTML = `
    <select id="hpc_preset"><option value="auto">auto</option><option value="custom">custom</option></select>
    <input id="hpc_partition" value="">
    <input id="hpc_time" value="">
    <input id="hpc_mem" value="">
    <input id="hpc_cpus" value="1">
    <input id="hpc_sbatch_gres" value="">
    <input id="hpc_job_name" value="">
    <input id="hpc_output_pattern" value="">
    <input id="hpc_error_pattern" value="">
    <input id="hpc_notify_email" value="">
    <textarea id="hpc_modules"></textarea>
    <textarea id="hpc_environment">{}</textarea>
    <input type="checkbox" id="hpc_monitor_jobs" checked>
    <input id="cohort_max_concurrent" value="50">
    <input id="cohort_batch_size" value="10">
`;

beforeEach(() => {
    document.body.innerHTML = FIXTURE_HTML;
    window.logHPC = vi.fn();
    window.applyHpcPreset = vi.fn();
    window.updateCohortDatasetDisplay = vi.fn();
    window.validateHpcEmailField = vi.fn();
    window.scheduleHpcPreflightRefresh = vi.fn();
    window._validateHpcTimeFormat = vi.fn().mockReturnValue(true);
    window._validateHpcMemFormat = vi.fn().mockReturnValue(true);
    window._parseHpcEnvironmentInput = vi.fn().mockReturnValue({ ok: true, environment: {} });
    loadScript('hpc_settings.js');
});

describe('loadHpcSettingsToForm', () => {
    it('populates cohort_batch_size from hpc.batch_size', () => {
        loadHpcSettingsToForm({ batch_size: 15, max_concurrent: 40 });

        expect(document.getElementById('cohort_batch_size').value).toBe('15');
        expect(document.getElementById('cohort_max_concurrent').value).toBe('40');
    });

    it('defaults cohort_batch_size to 10 when hpc.batch_size is missing', () => {
        loadHpcSettingsToForm({});

        expect(document.getElementById('cohort_batch_size').value).toBe('10');
    });

    it('preserves an explicit batch_size of 0 (batching disabled) instead of defaulting it away', () => {
        loadHpcSettingsToForm({ batch_size: 0 });

        expect(document.getElementById('cohort_batch_size').value).toBe('0');
    });
});

describe('getHpcSettingsFromForm', () => {
    function fillRequiredFields() {
        document.getElementById('hpc_partition').value = 'hpc';
        document.getElementById('hpc_time').value = '06:00:00';
        document.getElementById('hpc_mem').value = '16G';
        document.getElementById('hpc_cpus').value = '2';
    }

    it('includes batch_size in the returned settings object', () => {
        fillRequiredFields();
        document.getElementById('cohort_batch_size').value = '20';

        const settings = getHpcSettingsFromForm();

        expect(settings.batch_size).toBe(20);
    });

    it('accepts an explicit batch_size of 0', () => {
        fillRequiredFields();
        document.getElementById('cohort_batch_size').value = '0';

        const settings = getHpcSettingsFromForm();

        expect(settings.batch_size).toBe(0);
    });

    it('rejects a negative batch_size', () => {
        fillRequiredFields();
        document.getElementById('cohort_batch_size').value = '-1';

        const settings = getHpcSettingsFromForm();

        expect(settings).toBeNull();
        expect(window.logHPC).toHaveBeenCalledWith(expect.stringContaining('Batch size'), true);
    });

    it('rejects a non-numeric batch_size', () => {
        fillRequiredFields();
        document.getElementById('cohort_batch_size').value = 'abc';

        const settings = getHpcSettingsFromForm();

        expect(settings).toBeNull();
    });
});
