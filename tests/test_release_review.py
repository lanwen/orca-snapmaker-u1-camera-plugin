"""Release-note review follows the changes being released, not just nonempty text."""

from contextlib import redirect_stdout
import io
import json
import os
import re
from unittest.mock import patch

from test_release import ReleaseFixture
import release
from versioning import parse_change


class ReleaseReviewTests(ReleaseFixture):
    first = [("a" * 40, "fix: improve camera playback", parse_change("fix: improve camera playback"))]
    additional = [("b" * 40, "feat: add light control", parse_change("feat: add light control")), *first]
    public = "- Playback recovers after a connection interruption."

    def prepare(self, commits, previous=""):
        # Each automation refresh starts from a fresh checkout of main's development version.
        self.write_version("0.3.0.dev0")
        with patch.object(release, "history", return_value=("0.2.0", commits)):
            release.prepare_pr("release", previous_body=previous)
        return (self.root / "dist/automation-pr-body.md").read_text()

    def event(self, body):
        return {"pull_request": {
            "html_url": "https://github.com/example/plugin/pull/7",
            "title": "chore(release): prepare 0.3.0", "body": body,
            "head": {"ref": "automation/release", "repo": {"full_name": "example/plugin"}},
            "base": {"repo": {"full_name": "example/plugin"}}, "labels": [{"name": "release"}],
        }}

    def reviewed(self, body):
        # Simulate filling in the notes and ticking the task in GitHub's PR editor.
        body = body.replace(release.PUBLIC_PLACEHOLDER, self.public)
        self.assertRegex(body, r"(?m)^- \[ \] ", "The author gets a visible review task")
        return re.sub(r"(?m)^- \[ \] ", "- [x] ", body, count=1)

    def check(self, body, commits):
        with patch.object(release, "history", return_value=("0.2.0", commits)):
            release.check_pr(self.event(body))

    def test_missing_notes_have_a_pr_annotation_and_actionable_job_summary(self):
        body = self.prepare(self.first)
        event_file = self.root / "event.json"
        event_file.write_text(json.dumps(self.event(body)))
        summary_file = self.root / "summary.md"
        stdout = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_PATH": str(event_file),
                                     "GITHUB_STEP_SUMMARY": str(summary_file)}), \
                patch("sys.argv", ["release.py", "check-pr"]), redirect_stdout(stdout):
            with self.assertRaises(ValueError):
                release.main()
        self.assertIn("::error title=", stdout.getvalue(), "The PR check exposes a named error annotation")
        self.assertTrue(summary_file.is_file(), "The check gives instructions outside the raw log")
        summary = summary_file.read_text()
        self.assertIn(self.event(body)["pull_request"]["html_url"], summary)
        self.assertIn("Public notes", summary)
        self.assertIn("description", summary)

    def test_writing_notes_without_confirming_review_still_blocks_merge(self):
        body = self.prepare(self.first).replace(release.PUBLIC_PLACEHOLDER, self.public)
        with self.assertRaises(ValueError):
            self.check(body, self.first)

    def test_new_commits_preserve_text_but_require_a_new_review(self):
        reviewed = self.reviewed(self.prepare(self.first))
        self.check(reviewed, self.first)
        # Even before automation refreshes the body, the old review cannot approve new changes.
        with self.assertRaises(ValueError):
            self.check(reviewed, self.additional)
        refreshed = self.prepare(self.additional, reviewed)
        self.assertEqual(release.public_notes(refreshed), self.public)
        self.assertIn(self.additional[0][1], refreshed, "The new change is visible for review")
        with self.assertRaises(ValueError):
            self.check(refreshed, self.additional)
        self.check(self.reviewed(refreshed), self.additional)

    def test_refreshing_the_same_changes_preserves_the_review(self):
        reviewed = self.reviewed(self.prepare(self.first))
        refreshed = self.prepare(self.first, reviewed)
        self.assertEqual(release.public_notes(refreshed), self.public)
        self.check(refreshed, self.first)

    def test_adding_public_notes_cannot_be_bypassed_by_a_checked_task(self):
        body = self.reviewed(self.prepare(self.first)).replace(self.public, release.PUBLIC_PLACEHOLDER)
        with self.assertRaises(ValueError):
            self.check(body, self.first)
