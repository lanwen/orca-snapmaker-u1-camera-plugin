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

    @pytest.mark.parametrize('text', [
        pytest.param('', id='empty'),
        pytest.param('  \n ', id='whitespace'),
        pytest.param(PUBLIC_PLACEHOLDER, id='placeholder'),
        pytest.param('<!-- editor note -->', id='comment-only'),
    ])
    def test_empty_or_comment_only_notes_are_not_publishable(self, text):
        with pytest.raises(ValueError, match='Write public release notes'):
            public_notes(format_notes(text, "history"))

    @pytest.mark.parametrize('body', [
        pytest.param('<!-- orca:public-notes:start -->', id='missing-end'),
        pytest.param('<!-- orca:public-notes:end -->', id='missing-start'),
        pytest.param('<!-- orca:public-notes:end --><!-- orca:public-notes:start -->', id='reversed'),
        pytest.param('<!-- orca:public-notes:start -->text<!-- orca:public-notes:start --><!-- orca:public-notes:end -->', id='duplicate-start'),
        pytest.param('<!-- orca:public-notes:start --><!-- orca:public-notes:end --><!-- orca:public-notes:end -->', id='duplicate-end'),
    ])
    def test_malformed_markers_fail_instead_of_losing_human_edits(self, body):
        with pytest.raises(ValueError, match="marker"):
            public_section(body)

    def test_description_without_markers_has_no_public_notes(self):
        assert public_section("Generated description without public-note markers") is None
        with pytest.raises(ValueError, match="Missing tagged"):
            public_notes("Generated description without public-note markers")
