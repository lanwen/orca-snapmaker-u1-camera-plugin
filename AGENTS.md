# Agent instructions

When creating a PR or updating its description, read and use the repository's [pr skill](.agents/skills/pr/SKILL.md).
The GitHub [PR template](.github/pull_request_template.md) is the single source for the description format.

Render printer information only when a reliable source supplies it.
Hide information while its source is unavailable or status is pending; never fill gaps with guessed or stale values.

Use descriptive format, version, or compatibility terms instead of “legacy” in project-authored names, prose, comments, and fixture text.
Test observable behavior; use exact strings for actual contracts, and derive composed or preserved output from test inputs rather than locking incidental UI copy.

Don't run linting and formatting manually unless requested; they run through Git hooks at commit time.

For visual changes, use the [all-states HTML preview](docs/development.md#readme-screenshots) as the local feedback loop.
During UI iteration, rebuild the preview, check only the changed behavior, and share `dist/screenshots/index.html`.
Use `screenshots:review` from that guide for repeatable browser checks and captures.
At final handoff, run one complete desktop/narrow state review and required checks, then batch commits, pushes, screenshots, GIFs, and PR updates.
Repeat checks only when a new change could invalidate their results.
