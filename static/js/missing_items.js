// Formats a single `missing_items` entry from scripts/check_app_output.py's
// JSON output for display. Entries are `[SEVERITY] <message>` strings, and
// <message> is either single-line (FMRIPrepChecker, MRIQCChecker,
// CAT12Checker) or multi-line with indented "Subject:"/"Session:" fields
// (QSIPrepChecker, QSIReconChecker, FreeSurferChecker). Showing only the
// first line collapses two genuinely different entries (e.g. the same
// subject missing DWI in two different sessions) into identical-looking
// text, so this pulls out whatever disambiguates them.

function summarizeMissingItem(rawItem) {
    const withoutSeverity = String(rawItem || '')
        .replace(/^\[(ERROR|WARNING|INFO)\]\s*/, '');
    const lines = withoutSeverity.split('\n').map(l => l.trim()).filter(Boolean);
    const headline = lines[0] || '';

    const subjectLine = lines.slice(1).find(l => /^Subject:/i.test(l));
    const sessionLine = lines.slice(1).find(l => /^Session:/i.test(l));

    const parts = [];
    if (subjectLine) {
        parts.push(subjectLine.replace(/^Subject:\s*/i, '').trim());
    }
    if (sessionLine) {
        parts.push(sessionLine.replace(/^Session:\s*/i, '').trim());
    }

    if (!parts.length) {
        // Single-line checkers embed sub-XX/ses-YY directly in the message
        // rather than as separate indented fields.
        const subMatch = withoutSeverity.match(/sub-[A-Za-z0-9]+/);
        const sesMatch = withoutSeverity.match(/ses-[A-Za-z0-9]+/);
        if (subMatch) parts.push(subMatch[0]);
        if (sesMatch) parts.push(sesMatch[0]);
    }

    return parts.length ? `${headline} (${parts.join(', ')})` : headline;
}
