# Release automation

[Back to the README](../README.md) · [Development](development.md)

`pyproject.toml` is the version source for the plugin and its release tag.
Development uses Python's `.dev0` suffix, such as `0.3.0.dev0`.
Use Conventional Commit PR titles and squash merges so the merged commit carries that title.

1. A push to `main` opens or refreshes a signed release PR on `automation/release`.
   It chooses the version from changes since the last stable tag, removes `.dev0`, updates `uv.lock`, and adds a changelog section.
2. Merging the release PR publishes that exact merge commit as `v<version>`.
   The workflow verifies the files in a draft release before publishing it.
3. Successful publication opens a signed PR on `automation/next-snapshot` for the next patch development version.
   For example, release `0.3.0` is followed by `0.3.1.dev0`.
   The workflow waits for the snapshot commit's Check workflow to pass, then squash-merges that exact commit.
   Development versions are not published as GitHub releases.

Only snapshot PRs merge automatically.
Release PRs still require a manual merge.
The snapshot PR must change only `pyproject.toml` and `uv.lock`.
Failed or missing checks leave the PR open.

Release versions follow these Conventional Commit rules:

- `feat`: next minor version.
- Breaking changes, marked with `!`: next minor before 1.0; next major from 1.0 onward.
- `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `style`, `revert`, and `deps`: next patch version.

The current development target is a minimum.
The release PR can raise it for a feature or breaking change.
Generated release and snapshot commits are excluded from the next changelog and version calculation.
Normal PRs must retain a development version.
Only the generated release PR can carry a final version.
Automation changes `main` through PRs only.

Each release contains:

- `orca-snapmaker-u1-camera-plugin.py`
- `SHA256SUMS`

The stable project version, plugin metadata, changelog heading, and tag must agree.
Keep generated files out of commits.
Published assets are immutable: reruns verify their source commit and exact bytes without replacing them.
An interrupted draft upload can resume by rerunning the publishing job.

## Automation setup

Install the release GitHub App on this repository with write access to Contents, Issues, and Pull requests.
Set repository variable `RELEASE_APP_CLIENT_ID` and secret `RELEASE_APP_PRIVATE_KEY`.
The App creates signed version PRs whose normal PR checks can run.
Release PR automation stays disabled until the client ID is configured.
GitHub release publication uses the workflow's repository token.

To prepare version changes locally without creating a commit or PR:

```sh
mise run release:prepare-pr
```

This edits `pyproject.toml`, `uv.lock`, and `CHANGELOG.md` and writes the proposed PR body into ignored `dist/`.
It requires Git history and stable release tags in the local checkout.
The publishing fallback is `mise run release` from a clean checkout of the final release commit with GitHub CLI authentication.
It refuses development versions, mismatched tags, and changes to published assets.
