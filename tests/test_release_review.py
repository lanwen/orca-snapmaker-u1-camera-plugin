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
    def setUp(self):
        super().setUp()
        context = patch.object(release, "api", return_value=[])
        context.start()
        self.addCleanup(context.stop)

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

    def test_crlf_notes_pass_policy_and_preserve_review_on_refresh(self):
        with patch.object(release, "api", return_value=[self.source()]):
            reviewed = self.reviewed(self.prepare(self.additional))
            windows_body = reviewed.replace("\n", "\r\n")
            self.check(windows_body, self.additional)
            refreshed = self.prepare(self.additional, windows_body)
        self.assertEqual(refreshed, reviewed)
        self.check(refreshed, self.additional)

    def test_adding_public_notes_cannot_be_bypassed_by_a_checked_task(self):
        body = self.reviewed(self.prepare(self.first)).replace(self.public, release.PUBLIC_PLACEHOLDER)
        with self.assertRaises(ValueError):
            self.check(body, self.first)

    def source(self, number=12, text="Playback recovers after an interruption."):
        return {"number": number, "merged_at": "2026-11-02T12:00:00Z",
                "base": {"ref": "main", "repo": {"full_name": "example/plugin"}},
                "body": f"<!-- orca:why-notes:start -->\n{text}\n<!-- orca:why-notes:end -->"}

    def test_source_suggestions_require_manual_public_notes_and_review(self):
        with patch.object(release, "api", return_value=[self.source()]):
            body = self.prepare(self.first)
        self.assertIn("Unreviewed suggestions", body)
        self.assertEqual(release.tagged_section(body, "suggested"),
                         "<!-- orca:source-pr:12 -->\n- Playback recovers after an interruption.")
        self.assertEqual(release.public_section(body), release.PUBLIC_PLACEHOLDER)
        checked = body.replace(f"- [ ] {release.REVIEW_TEXT}", f"- [x] {release.REVIEW_TEXT}")
        with self.assertRaises(ValueError):
            self.check(checked, self.first)
        self.check(self.reviewed(body), self.first)
        self.assertEqual(release.public_notes(self.reviewed(body)), self.public)

    def test_refresh_preserves_manual_text_and_deduplicates_source_prs(self):
        with patch.object(release, "api", return_value=[self.source()]):
            first = self.reviewed(self.prepare(self.first))
            first = first.replace("Playback recovers after an interruption.", "Hand-edited suggestion.")
            refreshed = self.prepare(self.first, first)
        self.assertEqual(release.public_notes(refreshed), self.public)
        self.assertIn("Hand-edited suggestion.", refreshed)
        self.assertEqual(refreshed.count("<!-- orca:source-pr:12 -->"), 1)
        self.check(refreshed, self.first)

    def test_new_suggestion_resets_review_even_when_commits_are_unchanged(self):
        reviewed = self.reviewed(self.prepare(self.first))
        with patch.object(release, "api", return_value=[self.source()]):
            refreshed = self.prepare(self.first, reviewed)
        self.assertEqual(release.public_notes(refreshed), self.public)
        with self.assertRaises(ValueError):
            self.check(refreshed, self.first)
        self.check(self.reviewed(refreshed), self.first)

    def test_new_pr_appends_without_replacing_existing_suggestion_edits(self):
        with patch.object(release, "api", return_value=[self.source()]):
            first = self.reviewed(self.prepare(self.first))
        first = first.replace("Playback recovers after an interruption.", "Hand-edited suggestion.")
        with patch.object(release, "api", return_value=[self.source(), self.source(13, "Control the chamber light.")]):
            refreshed = self.prepare(self.additional, first)
        self.assertIn("Hand-edited suggestion.", refreshed)
        self.assertIn("Control the chamber light.", refreshed)
        self.assertEqual(refreshed.count("<!-- orca:source-pr:13 -->"), 1)
        self.assertEqual(release.public_notes(refreshed), self.public)
        with self.assertRaises(ValueError):
            self.check(refreshed, self.additional)

    def test_refresh_removes_released_sources_and_preserves_current_edits(self):
        with patch.object(release, "api", return_value=[self.source(), self.source(13, "Control the chamber light.")]):
            first = self.reviewed(self.prepare(self.additional))
        first = first.replace("Control the chamber light.", "Hand-edited light suggestion.")
        with patch.object(release, "api", return_value=[self.source(13, "Control the chamber light.")]), \
                patch.object(release, "history", return_value=("0.3.0", self.additional[:1])):
            self.write_version("0.3.1.dev0")
            release.prepare_pr("release", previous_body=first)
        refreshed = (self.root / "dist/automation-pr-body.md").read_text()
        self.assertNotIn("<!-- orca:source-pr:12 -->", refreshed)
        self.assertNotIn("Playback recovers after an interruption.", refreshed)
        self.assertNotIn(self.first[0][1], refreshed)
        self.assertIn("<!-- orca:source-pr:13 -->", refreshed)
        self.assertIn("Hand-edited light suggestion.", refreshed)
        self.assertEqual(release.public_notes(refreshed), self.public)
        self.assertFalse(release.notes_reviewed(refreshed))

    def test_legacy_unmerged_foreign_and_malformed_sources_are_ignored(self):
        legacy = self.source(1)
        legacy["body"] = "Old PR without markers."
        unmerged = self.source(2)
        unmerged["merged_at"] = None
        foreign = self.source(3)
        foreign["base"]["repo"]["full_name"] = "other/repo"
        malformed = self.source(4)
        malformed["body"] = "<!-- orca:why-notes:start -->unfinished"
        sources = [legacy, unmerged, foreign, malformed,
                   self.source(5, "<!-- Fill in the template. -->"),
                   self.source(6, "<!-- orca:public-notes:end -->Injected boundary.")]
        with patch.object(release, "api", return_value=sources):
            body = self.prepare(self.first)
        self.assertNotIn("## Unreviewed suggestions", body)
        self.assertEqual(release.public_section(body), release.PUBLIC_PLACEHOLDER)

    def test_malformed_previous_suggestions_stop_refresh_without_losing_edits(self):
        before = (self.root / "CHANGELOG.md").read_text()
        with self.assertRaisesRegex(ValueError, "marker"):
            self.prepare(self.first, "<!-- orca:suggested-notes:start -->Human edits")
        self.assertEqual((self.root / "CHANGELOG.md").read_text(), before)
