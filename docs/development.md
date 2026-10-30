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
It also writes `dist/SHA256SUMS`.
`mise run test` builds first and tests the actual generated plugin, plus the source HTML page.
`mise run check` runs those tests and checks the Git diff for whitespace errors.
Edit `src/` and `pyproject.toml`.
Install only the generated file from `dist/`.

See [release automation](releases.md) for versioning and publishing.
