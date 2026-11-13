"""Public-note boundaries, preservation, and export validation."""

from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from release_notes import PUBLIC_PLACEHOLDER, format_notes, public_notes, public_section


class TestReleaseNotes:
    def test_unicode_and_paragraphs_survive_export_without_generated_history(self):
        text = "- See progress → finish.\n\nPrinting is blue; completion is green."
        body = format_notes(text, "fix(ci): internal links") + "\nSource commit: abc."
        assert public_notes(body) == text
        assert public_section(body) == text

    def test_boundary_is_not_silently_truncated(self):
        text = "é" * 4000
        assert public_notes(format_notes(text, 'history')) == text
        with pytest.raises(ValueError, match='4000'):
            public_notes(format_notes(text + "x", "history"))

    @pytest.mark.parametrize('text', ['', '  \n ', PUBLIC_PLACEHOLDER, '<!-- editor note -->'], ids=['empty', 'n', 'PUBLIC_PLACEHOLDER', 'editor-note'])
    def test_empty_or_comment_only_notes_are_not_publishable(self, text):
        with pytest.raises(ValueError, match='Write public release notes'):
            public_notes(format_notes(text, "history"))

    def test_malformed_markers_fail_instead_of_losing_human_edits(self):
        start, end = "<!-- orca:public-notes:start -->", "<!-- orca:public-notes:end -->"
        for body in [start, end, end + start, start + "text" + start + end, start + end + end]:
            with pytest.raises(ValueError, match='marker'):
                public_section(body)
        assert public_section('Legacy generated description') is None
        with pytest.raises(ValueError, match='Missing tagged'):
            public_notes("Legacy generated description")
