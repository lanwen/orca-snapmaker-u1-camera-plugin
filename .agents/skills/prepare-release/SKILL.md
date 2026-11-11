---
name: prepare-release
description: Find the latest pending release PR and prepare its public release notes from generated changes and suggestions.
---

# Prepare release

## Find the release PR

Find the newest open release PR in this repository:

```sh
gh pr list --state open --base main --head automation/release --label release --json number,title,url,createdAt --limit 20
```

Select the newest by creation date and confirm its head belongs to this repository.
If none exists, report that there is no pending release PR and stop.
Read its full description, including existing **Public notes**, **Generated notes**, and **Unreviewed suggestions**.

## Prepare public notes

Follow [Public release notes](../../../docs/releases.md#public-release-notes).
Use the suggestions as a starting point and compare them with the generated changes; inspect source PRs when a claim needs clarification.
Preserve useful existing public notes, combine duplicates, and write short bullets about what users can see or do.
Leave commit hashes, PR attribution, and internal maintenance details out of the public text.
For a maintenance-only release, write a short, accurate maintenance note.

Replace only the content between `<!-- orca:public-notes:start -->` and `<!-- orca:public-notes:end -->`.
Keep the release PR's existing format, generated notes, suggestions, source-PR comments, and all marker pairs intact.
Public notes must be nonempty and contain no placeholder.
Keep them as short as possible; 4,000 characters is a hard limit, not a target.
After comparing every generated change with the public notes, check `I reviewed the public notes against the generated changes below.`

Re-read the PR before saving; reconcile any new changes or suggestions before marking the notes reviewed.
Save the updated description with `gh pr edit --body-file` using a temporary file, then remove that file and confirm the saved notes and review checkbox.
Return the release PR link and the public notes.
Merging the PR or publishing a release requires a separate request.
