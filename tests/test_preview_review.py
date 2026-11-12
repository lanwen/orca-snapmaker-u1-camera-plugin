"""Exercise the browser review's result checks and DevTools protocol handling."""

import errno
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import review_preview
from review_preview import DevTools, check_alignment, browser, _remove_profile, _stop_browser


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


class BrowserCleanupTests(unittest.TestCase):
    def test_profile_removal_retries_recreated_directory_and_busy_files(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            profile.mkdir()
            remove = review_preview.shutil.rmtree
            attempts = []

            def delayed_remove(path):
                attempts.append(path)
                if len(attempts) == 1:
                    (profile / "late-write").write_text("child flush")
                    raise OSError(errno.ENOTEMPTY, "directory was repopulated")
                if len(attempts) == 2:
                    raise OSError(errno.EBUSY, "child still releasing profile")
                remove(path)

            with patch.object(review_preview.shutil, "rmtree", side_effect=delayed_remove), \
                 patch.object(review_preview.time, "sleep") as pause:
                _remove_profile(profile)
            self.assertEqual(len(attempts), 3)
            self.assertEqual(pause.call_count, 2)
            self.assertFalse(profile.exists())

    def test_profile_removal_reports_exhausted_retry_deadline(self):
        error = OSError(errno.ENOTEMPTY, "persistent writer")
        with patch.object(review_preview.shutil, "rmtree", side_effect=error) as remove, \
             patch.object(review_preview.time, "monotonic", side_effect=[0, 0, 6]), \
             patch.object(review_preview.time, "sleep"):
            with self.assertRaises(OSError) as caught:
                _remove_profile("profile")
        self.assertIs(caught.exception, error)
        self.assertEqual(remove.call_count, 2)

    def test_profile_removal_does_not_retry_permission_errors(self):
        with patch.object(review_preview.shutil, "rmtree", side_effect=PermissionError(errno.EACCES, "denied")) as remove, \
             patch.object(review_preview.time, "sleep") as pause:
            with self.assertRaises(PermissionError):
                _remove_profile("profile")
        remove.assert_called_once()
        pause.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "Process groups require POSIX")
    def test_graceful_close_waits_for_browser_then_delayed_children(self):
        process, connection = Mock(pid=4242), Mock()
        events = Mock()
        events.attach_mock(connection.send, "send")
        events.attach_mock(process.wait, "wait")
        with patch.object(review_preview.os, "killpg", side_effect=[None, None, ProcessLookupError()]) as killpg, \
             patch.object(review_preview.time, "sleep") as pause:
            _stop_browser(process, connection)
        self.assertEqual(json.loads(events.mock_calls[0].args[0])["method"], "Browser.close")
        self.assertEqual(events.mock_calls[1], call.wait(timeout=5))
        self.assertEqual(killpg.call_args_list, [call(4242, signal.SIGTERM), call(4242, 0), call(4242, 0)])
        pause.assert_called_once()
        connection.recv.assert_not_called()  # Closing Chrome need not acknowledge CDP.

    @unittest.skipUnless(os.name == "posix", "Process groups require POSIX")
    def test_lost_transport_and_hung_browser_force_only_owned_group(self):
        process, connection = Mock(pid=4242), Mock()
        connection.send.side_effect = OSError("connection gone")
        connection.close.side_effect = OSError("already closed")
        process.wait.side_effect = [subprocess.TimeoutExpired("chrome", 5),
                                    subprocess.TimeoutExpired("chrome", 5), 0]
        with patch.object(review_preview.os, "killpg", side_effect=[None, None, ProcessLookupError(), ProcessLookupError()]) as killpg:
            _stop_browser(process, connection)
        self.assertEqual(killpg.call_args_list, [call(4242, signal.SIGTERM), call(4242, signal.SIGKILL),
                                               call(4242, signal.SIGTERM), call(4242, 0)])
        self.assertEqual(process.wait.call_args_list, [call(timeout=5)] * 3)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "Process groups require POSIX")
    def test_children_that_outlive_parent_have_bounded_shutdown(self):
        process = Mock(pid=4242)
        with patch.object(review_preview.os, "killpg") as killpg, \
             patch.object(review_preview.time, "monotonic", side_effect=[0, 2]):
            _stop_browser(process, None)
        self.assertEqual(killpg.call_args_list, [call(4242, signal.SIGTERM), call(4242, 0),
                                               call(4242, signal.SIGKILL)])

    def run_context(self, profile, failure=None):
        (profile / "DevToolsActivePort").write_text("1234\n/browser/test")
        response = io.BytesIO(json.dumps([{"type": "page", "url": "about:blank",
                                          "webSocketDebuggerUrl": "ws://localhost/page/test"}]).encode())
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(review_preview.tempfile, "mkdtemp", return_value=str(profile)), \
             patch.object(review_preview, "find_browser", return_value="chrome"), \
             patch.object(review_preview.subprocess, "Popen") as launch, \
             patch.object(review_preview.urllib.request, "build_opener", return_value=opener), \
             patch.object(review_preview.websocket, "create_connection"), \
             patch.object(review_preview, "_stop_browser") as stop, \
             patch.object(review_preview, "_remove_profile", side_effect=OSError(errno.ENOTEMPTY, "still busy")):
            with browser():
                self.assertEqual(launch.call_args.kwargs["start_new_session"], os.name == "posix")
                if failure is not None:
                    raise failure
        return stop

    def test_body_failure_stays_primary_when_profile_cleanup_fails(self):
        original = AssertionError("CSP assertion failed")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AssertionError) as caught:
                self.run_context(Path(directory), original)
        self.assertIs(caught.exception, original)
        self.assertIn("Browser cleanup failed", original.__notes__[0])
        self.assertIn("still busy", original.__notes__[0])

    def test_cleanup_failure_fails_an_otherwise_successful_context(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(OSError, "still busy") as caught:
                self.run_context(Path(directory))
        self.assertIn("Browser cleanup failed", caught.exception.__notes__[0])

    def test_browser_exit_reports_executable_return_code_and_output(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(review_preview.tempfile, "mkdtemp", return_value=directory), \
             patch.object(review_preview, "find_browser", return_value="/usr/bin/google-chrome"), \
             patch.object(review_preview.subprocess, "Popen") as launch, \
             patch.object(review_preview, "_stop_browser"), \
             patch.object(review_preview, "_remove_profile"):
            def exited(*args, **kwargs):
                kwargs["stderr"].write(b"browser startup diagnostic")
                return Mock(poll=Mock(return_value=23))
            launch.side_effect = exited
            with self.assertRaisesRegex(RuntimeError, "/usr/bin/google-chrome.*exit code 23.*browser startup diagnostic"):
                with browser():
                    self.fail("Exited browser cannot become ready")

    def test_startup_failure_stays_primary_and_cleanup_is_attempted(self):
        original = FileNotFoundError("chrome missing")
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(review_preview.tempfile, "mkdtemp", return_value=directory), \
             patch.object(review_preview, "find_browser", side_effect=original), \
             patch.object(review_preview, "_remove_profile", side_effect=OSError(errno.ENOTEMPTY, "still busy")) as remove:
            with self.assertRaises(FileNotFoundError) as caught:
                with browser():
                    self.fail("Startup must fail before yielding")
        self.assertIs(caught.exception, original)
        remove.assert_called_once_with(directory)
        self.assertIn("Browser cleanup failed", original.__notes__[0])
