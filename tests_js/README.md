# JS test suite

Tests for the extracted `static/js/*.js` modules (plain classic
`<script src>` files, not ES modules -- see each module's own header
comment). Runs under [Vitest](https://vitest.dev/) with a jsdom
environment; `tests_js/helpers/loadScript.js` evaluates a module's source
via `window.eval()` so its top-level `function`/`var` declarations become
real globals, exactly like loading it via `<script src>` in the browser.

## One-time setup (no root/sudo required)

This machine has no system Node.js (and no sudo to install one), so a
portable build is downloaded straight from nodejs.org into `.node-runtime/`
at the repo root -- gitignored, same spirit as `.datalad-slurm-venv/`.

```bash
NODE_VERSION=v24.18.0
curl -fsSL "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/node.tar.xz
mkdir -p .node-runtime
tar -xJf /tmp/node.tar.xz -C .node-runtime --strip-components=1
export PATH="$(pwd)/.node-runtime/bin:$PATH"
npm install
```

(Different OS/arch: swap `linux-x64` for the right build under
https://nodejs.org/dist/latest-v24.x/.)

## Running

```bash
export PATH="$(pwd)/.node-runtime/bin:$PATH"   # once per shell
npm test              # vitest run -- one-shot
npx vitest            # watch mode
```

## Writing a new test for a `static/js/*.js` module

```js
import { loadScript } from './helpers/loadScript.js';

beforeEach(() => {
    document.body.innerHTML = '...fixture markup with the ids the module reads...';
    loadScript('your_module.js');
});
```

If the module calls helpers that live inline in `templates/index.html`
(not extractable into `static/js/`, e.g. `isQsireconContainer()`,
`inferCurrentPipelineAppName()`), stub them as `window.fnName = () => ...`
before calling into the module -- see `pipeline_form_sync.test.js` for the
pattern. Helpers that DO live in `static/js/` (e.g. `project_loader.js`'s
`cloneJson`/`normalizePipelineEntry`) should be loaded for real via
`loadScript(...)` rather than stubbed, for a more faithful test.
