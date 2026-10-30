# Release automation

[Back to the README](../README.md) · [Development](development.md)

`pyproject.toml` is the version source for the plugin and its release tag.
Development uses Python's `.dev0` suffix, such as `0.3.0.dev0`.
Use Conventional Commit PR titles and squash merges so the merged commit carries that title.

1. A push to `main` opens or refreshes a signed release PR on `automation/release`.
   It chooses the version from changes since the last stable tag, removes `.dev0`, updates `uv.lock`, and adds a changelog section.
2. Merging the release PR publishes that exact merge commit as `v<version>`.
   The workflow verifies the files in a draft release before publishing it.
3. A separate `publish-cloud` job waits for GitHub publication and uploads the verified release artifact to Orca Cloud.
4. After validation of a final version on `main`, snapshot preparation runs independently of both publishing jobs.
   It opens a signed PR on `automation/next-snapshot` for the next patch development version.
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

New releases contain:

- `orca-snapmaker-u1-camera-plugin_any.py`
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

## Orca Cloud publishing

In Orca Cloud, open Shared Plugins → your plugin → Edit plugin → GitHub publishing.
Connect `lanwen/orca-snapmaker-u1-camera-plugin` to the existing listing.
No Orca Cloud secret is required.
See the [trusted publishing guide](https://cloud.orcaslicer.com/wiki/#publish-from-github).

The `publish-cloud` job depends on the GitHub `publish` job.
Only the Cloud job grants `id-token: write`; its repository access is read-only.
It runs `mise run release:publish-cloud`, which downloads the released plugin and `SHA256SUMS` from GitHub.
It requests a short-lived GitHub OIDC token with audience `orcacloud`.
It verifies the source commit and checks the downloaded plugin against `SHA256SUMS` before uploading those exact bytes.
The version comes from `pyproject.toml`, and the changelog comes from the published GitHub release notes.
It does not use `GITHUB_REF_NAME` because this workflow runs on `main`, not a release tag.
The upload runs directly in this workflow, so it does not depend on a separate `release: published` event.

Cloud failures leave the GitHub release intact.
Snapshot preparation does not wait for publication and can advance the development version even when a publishing job fails.
After fixing a failure, rerun the failed `publish-cloud` job from the original release run.
The successful GitHub publishing job does not need to run again.
The Cloud version must be greater than the listing's current version.
Orca Cloud rejects duplicate versions without replacing them, so rerunning an already successful Cloud upload reports an error.
If only snapshot preparation fails, rerun that failed job instead of the successful publishing job.
