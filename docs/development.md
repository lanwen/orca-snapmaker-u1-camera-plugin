# Development

[Back to the README](../README.md)

Source files are kept separately from the installable plugin:

```text
src/plugin.py         Camera logic, worker, and Orca capability
src/http_client.py    Typed Moonraker requests, HTTP/WebSocket transport, and authentication
src/camera.html       Camera page, styles, and browser code
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

`mise run setup` creates the isolated Python environment from `uv.lock`.
`mise run build` embeds `src/camera.html` into the generated Python plugin and adds Orca's metadata from `pyproject.toml`.
The filename ends in `_any.py` for Orca Cloud's universal platform convention.
It also writes `dist/SHA256SUMS`.
`mise run test` builds first and tests the actual generated plugin, plus the source HTML page.
`mise run check` runs those tests and checks the Git diff for whitespace errors.
Edit `src/` and `pyproject.toml`.
Install only the generated file from `dist/`.

See [release automation](releases.md) for versioning and publishing.

## README screenshots

Run `mise run screenshots:prepare`, then open `dist/screenshots/index.html` in a browser.
The four standalone views include a simulated Orca window and the selected Snapmaker U1 tab.
They use the production camera page with fixed sample data and make no printer requests.
No Orca installation is needed.

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

PNG export uses an installed Chrome or Chromium with an isolated temporary profile.
The generated `dist/screenshots/showcase.html` shows one full camera view and enlarged crops of the four state strips.
This keeps the tab visible while making the statistics readable at README width.
