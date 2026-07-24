// static/js/*.js are plain global-scope scripts loaded via <script src> in
// templates/index.html -- not ES modules, no export/import. Under Vitest's
// jsdom environment, `window` in the test file IS the jsdom window backing
// the global scope, so evaluating a script's source via window.eval() (the
// same non-strict, "as if loaded via <script>" execution jsdom itself would
// do) defines its top-level function/var declarations as real globals,
// exactly like the browser does.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STATIC_JS_DIR = path.resolve(__dirname, '../../static/js');

export function loadScript(filename) {
    const src = fs.readFileSync(path.join(STATIC_JS_DIR, filename), 'utf8');
    // eslint-disable-next-line no-eval
    window.eval(src);
}
