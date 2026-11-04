"""Exercise the browser review's result checks and DevTools protocol handling."""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from review_preview import DevTools, check_alignment


class PreviewReviewTests(unittest.TestCase):
    def test_missing_estimates_do_not_hide_a_shifted_bar(self):
        rows = [
            {"bar": {"x": 10, "y": 20, "width": 300}, "estimate": None},
            {"bar": {"x": 10, "y": 21, "width": 300}, "estimate": None},
        ]
        with self.assertRaisesRegex(ValueError, "bar y"):
            check_alignment(rows)

    def test_plan_and_remaining_values_use_the_same_height(self):
        rows = [
            {"bar": None, "estimate": {"y": 10, "height": 26}},
            {"bar": None, "estimate": {"y": 10, "height": 31}},
        ]
        with self.assertRaisesRegex(ValueError, "estimate height"):
            check_alignment(rows)

    def test_devtools_ignores_events_and_reports_javascript_errors(self):
        connection = Mock()
        connection.recv.side_effect = [json.dumps(message) for message in [
            {"method": "Page.loadEventFired"},
            {"id": 1, "result": {"result": {"value": 42}}},
            {"id": 2, "result": {"exceptionDetails": {"text": "Uncaught error"}}},
        ]]
        client = DevTools(connection)
        self.assertEqual(client.evaluate("40+2"), 42)
        with self.assertRaisesRegex(RuntimeError, "Uncaught error"):
            client.evaluate("broken()")
