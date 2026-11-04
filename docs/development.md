# Development

[Back to the README](../README.md)

Source files are kept separately from the installable plugin:

```text
src/plugin.py         Camera logic, worker, and Orca capability
src/http_client.py    Typed Moonraker requests, HTTP/WebSocket transport, and authentication
src/camera.html       Self-contained HTML shell and content security policy
src/ui/               Svelte components, TypeScript Orca bridge, and camera styles
package.json          Frontend scripts and development dependencies
pnpm-lock.yaml        Locked frontend dependencies
vite.config.ts        Self-contained camera page build and UI test configuration
pyproject.toml        Plugin metadata and Python dependencies
scripts/build.py      Single-file plugin builder
scripts/release.py    Release PR preparation and immutable publication
scripts/versioning.py Conventional Commit version rules
mise.toml             Tools and development tasks
tests/                Client, worker, build, and page checks
dist/                 Generated plugin and SHA256SUMS; ignored by Git
```

Install [mise](https://mise.jdx.dev/getting-started.html), then run:

```sh
mise install
mise run check
```

`mise run setup` installs Python dependencies from `uv.lock` and frontend dependencies with `pnpm install --frozen-lockfile`.
It also installs the repository's pre-commit hook through the local Git `core.hooksPath` setting.
The hook runs Oxfmt checks and Oxlint, including the security rules below, before each commit, using the tools pinned by mise.
It checks the frontend working tree, including unstaged edits, and rejects the commit if either check fails.
It does not format or stage files automatically.
If formatting fails, run `mise exec -- pnpm format`, review and stage the result, then commit again.
You can run the same checks directly with `mise exec -- pnpm check:commit`.
`mise run build` compiles the Svelte UI into `dist/ui/camera.html`, embeds that page into the generated Python plugin, and adds Orca's metadata from `pyproject.toml`.
The compiled page includes all JavaScript and CSS, uses the existing content security policy, and needs no network access for assets.
Node and pnpm are development tools; Orca users still install one Python file.
The filename ends in `_any.py` for Orca Cloud's universal platform convention.
It also writes `dist/SHA256SUMS`.
`mise run test` builds first and tests the actual generated plugin and Svelte UI.
`mise run check` runs the Python tests, Oxfmt formatting checks, Oxlint lint checks, Svelte and TypeScript checks, UI behavior tests, and Git whitespace checks.
Edit `src/` and `pyproject.toml`.
Install only the generated file from `dist/`.

For a live UI preview with offline sample data, run:

```sh
mise exec -- pnpm dev
```

Vite reloads the page when you edit a component.
Keep component-specific styles in that component's scoped `<style>` block.
`src/ui/global.css` contains document defaults, the shared hidden-element rule, and shared button styles.
The preview bridge does not connect to Orca or a printer.
Use `mise exec -- pnpm run check` for formatting, lint, and type checks, and `mise exec -- pnpm test` for UI tests.
Run `mise exec -- pnpm format` to format the frontend, or `mise exec -- pnpm format:check` to check it without changing files.
Run `mise exec -- pnpm lint` for Oxlint alone, or `mise exec -- pnpm check:types` for Svelte and TypeScript checks.
Oxfmt covers Svelte, TypeScript, JavaScript, CSS, and the frontend HTML and JSON files.
Oxlint checks JavaScript, TypeScript, and Svelte script blocks; `svelte-check` supplies template diagnostics.
The lint command rejects dynamic code execution, JavaScript URLs, legacy prototype mutation, and direct HTML insertion through browser APIs.
Selected `eslint-plugin-security` rules also check for unsafe regular expressions, bidirectional control characters, unsafe child-process calls, dynamic `require` calls, and unsafe Buffer and random-byte APIs.
HTML insertion is deliberately prohibited in scripts: use Svelte markup for structure and text bindings or `textContent` for data.
These are static source checks; they do not audit dependency vulnerabilities, scan secrets, analyze Python, or inspect Svelte template HTML injection.
Formatting and lint commands target the frontend and its tests, leaving prototypes, generated files, and Python sources alone.
The UI tests cover playback, light control, print statistics, frame acknowledgements, visibility, and printer switching.

See [release automation](releases.md) for versioning and publishing.

## README screenshots

Run `mise run screenshots:prepare`, then open `dist/screenshots/index.html` in a browser.
Use the preview state picker to inspect all print states, including heating, warm preparation, unavailable estimates, errors, and idle status.
The four-state showcase remains a separate view for README screenshots.
The four standalone views include a simulated Orca window and the selected Snapmaker U1 tab.
They use the production camera page with fixed sample data and make no printer requests.
No Orca installation is needed.

For repeated reviews with dependencies already installed, rebuild the self-contained preview directly:

```sh
mise exec -- python scripts/build.py
mise exec -- python scripts/prepare_screenshots.py
```

Open or refresh `dist/screenshots/index.html` and review affected states at desktop and narrow widths.

Use the maintained headless review command with prepared HTML and installed dependencies:

```sh
mise run screenshots:review -- --states printing preparing paused
```

Omit `--states` for a final review of all states at 1100px and 390px widths.
Use `--widths 1440 390` to choose other widths.
The command reuses local Chrome or Chromium with a temporary profile, opens local files without a server, and checks alignment, overflow, shimmer movement, and reduced motion.
It writes state screenshots, a review gallery, and measured results to `dist/preview-review/`.
Open `dist/preview-review/index.html` to inspect the rendered typography and spacing; automated geometry checks do not replace that visual review.
It does not rebuild the HTML or install dependencies.
For publication, add `--gifs` to capture animated states at the first width; this requires `ffmpeg`.
Generate README and PR assets once after the visual iteration is complete.

The default camera image and app chrome come from the existing README screenshot.
To use another local camera frame, run `python scripts/prepare_screenshots.py --camera /path/to/frame.jpg`.

Click the camera to focus the page.
Press **1** for Printing, **2** for Preparing, **3** for Paused, or **4** for Complete.
Press **H** to show or hide the stats background, or **?** to show or hide shortcut help.

Use the same browser size for all four captures, for example 1440 × 1000.
Hide help, move the pointer away, and capture only the page content so that the simulated Orca tabs remain visible.
These images illustrate sample states; they are not evidence of live print jobs.

To regenerate the four-state image used in the README, run:

```sh
python scripts/prepare_screenshots.py --export docs/images/snapmaker-u1-states.png
```

Build first with `mise run build` when calling `prepare_screenshots.py` directly.
The screenshot tool loads the compiled production UI with an offline Orca bridge.

PNG export uses an installed Chrome or Chromium with an isolated temporary profile.
The generated `dist/screenshots/showcase.html` shows Printing in one full camera view, followed by enlarged Preparing, Paused, and Complete strips.
This keeps the tab visible while making the statistics readable at README width.
