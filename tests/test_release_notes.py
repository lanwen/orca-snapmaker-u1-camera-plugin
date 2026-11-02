"""Public-note boundaries, preservation, and export validation."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from release_notes import PUBLIC_PLACEHOLDER, format_notes, public_notes, public_section


class ReleaseNotesTests(unittest.TestCase):
    def test_unicode_and_paragraphs_survive_export_without_generated_history(self):
        text = "- See progress → finish.\n\nPrinting is blue; completion is green."
        body = format_notes(text, "fix(ci): internal links") + "\nSource commit: abc."
        self.assertEqual(public_notes(body), text)
        self.assertEqual(public_section(body), text)

    def test_boundary_is_not_silently_truncated(self):
        text = "é" * 4000
        self.assertEqual(public_notes(format_notes(text, "history")), text)
        with self.assertRaisesRegex(ValueError, "4000"):
            public_notes(format_notes(text + "x", "history"))

    def test_empty_or_comment_only_notes_are_not_publishable(self):
        for text in ["", "  \n ", PUBLIC_PLACEHOLDER, "<!-- editor note -->"]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "Write public release notes"):
                public_notes(format_notes(text, "history"))

    def test_malformed_markers_fail_instead_of_losing_human_edits(self):
        start, end = "<!-- orca:public-notes:start -->", "<!-- orca:public-notes:end -->"
        for body in [start, end, end + start, start + "text" + start + end, start + end + end]:
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "marker"):
                public_section(body)
        self.assertIsNone(public_section("Legacy generated description"))
        with self.assertRaisesRegex(ValueError, "Missing tagged"):
            public_notes("Legacy generated description")
