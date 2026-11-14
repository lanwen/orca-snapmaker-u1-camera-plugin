"""Exercise the browser review's result checks and DevTools protocol handling."""

from contextlib import contextmanager
import errno
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
import pytest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import review_preview
from review_preview import DevTools, check_alignment, browser, _remove_profile, _stop_browser


class TestPreviewReview:
    def test_missing_estimates_do_not_hide_a_shifted_bar(self):
        rows = [
            {"bar": {"x": 10, "y": 20, "width": 300}, "estimate": None},
            {"bar": {"x": 10, "y": 21, "width": 300}, "estimate": None},
        ]
        with pytest.raises(ValueError, match='bar y'):
            check_alignment(rows)

    def test_plan_and_remaining_values_use_the_same_height(self):
        rows = [
            {"bar": None, "estimate": {"y": 10, "height": 26}},
            {"bar": None, "estimate": {"y": 10, "height": 31}},
        ]
        with pytest.raises(ValueError, match='estimate height'):
            check_alignment(rows)

    def test_devtools_ignores_events_and_reports_javascript_errors(self):
        connection = Mock()
        failure = {"text": "Injected JavaScript failure"}
        connection.recv.side_effect = [json.dumps(message) for message in [
            {"method": "Page.loadEventFired"},
            {"id": 1, "result": {"result": {"value": 42}}},
            {"id": 2, "result": {"exceptionDetails": failure}},
        ]]
        client = DevTools(connection)
        assert client.evaluate('40+2') == 42
        with pytest.raises(RuntimeError) as raised:
            client.evaluate("broken()")
        assert raised.value.args == (failure,)


class TestBrowserCleanup:
    def test_profile_removal_retries_recreated_directory_and_busy_files(self, tmp_path):
        directory = str(tmp_path)
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
        assert len(attempts) == 3
        assert pause.call_count == 2
        assert not profile.exists()

    def test_profile_removal_reports_exhausted_retry_deadline(self):
        error = OSError(errno.ENOTEMPTY, "persistent writer")
        with patch.object(review_preview.shutil, "rmtree", side_effect=error) as remove, \
             patch.object(review_preview.time, "monotonic", side_effect=[0, 0, 6]), \
             patch.object(review_preview.time, "sleep"):
            with pytest.raises(OSError) as caught:
                _remove_profile("profile")
        assert caught.value is error
        assert remove.call_count == 2

    def test_profile_removal_does_not_retry_permission_errors(self):
        with patch.object(review_preview.shutil, "rmtree", side_effect=PermissionError(errno.EACCES, "denied")) as remove, \
             patch.object(review_preview.time, "sleep") as pause:
            with pytest.raises(PermissionError):
                _remove_profile("profile")
        remove.assert_called_once()
        pause.assert_not_called()

    @pytest.mark.skipif(not (os.name == 'posix'), reason='Process groups require POSIX')
    def test_graceful_close_waits_for_browser_then_delayed_children(self):
        process, connection = Mock(pid=4242), Mock()
        events = Mock()
        events.attach_mock(connection.send, "send")
        events.attach_mock(process.wait, "wait")
        with patch.object(review_preview.os, "killpg", side_effect=[None, None, ProcessLookupError()]) as killpg, \
             patch.object(review_preview.time, "sleep") as pause:
            _stop_browser(process, connection)
        assert json.loads(events.mock_calls[0].args[0])['method'] == 'Browser.close'
        assert events.mock_calls[1] == call.wait(timeout=5)
        assert killpg.call_args_list == [call(4242, signal.SIGTERM), call(4242, 0), call(4242, 0)]
        pause.assert_called_once()
        connection.recv.assert_not_called()  # Closing Chrome need not acknowledge CDP.

    @pytest.mark.skipif(not (os.name == 'posix'), reason='Process groups require POSIX')
    def test_lost_transport_and_hung_browser_force_only_owned_group(self):
        process, connection = Mock(pid=4242), Mock()
        connection.send.side_effect = OSError("connection gone")
        connection.close.side_effect = OSError("already closed")
        process.wait.side_effect = [subprocess.TimeoutExpired("chrome", 5),
                                    subprocess.TimeoutExpired("chrome", 5), 0]
        with patch.object(review_preview.os, "killpg", side_effect=[None, None, ProcessLookupError(), ProcessLookupError()]) as killpg:
            _stop_browser(process, connection)
        assert killpg.call_args_list == [call(4242, signal.SIGTERM), call(4242, signal.SIGKILL), call(4242, signal.SIGTERM), call(4242, 0)]
        assert process.wait.call_args_list == [call(timeout=5)] * 3
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    @pytest.mark.skipif(not (os.name == 'posix'), reason='Process groups require POSIX')
    def test_children_that_outlive_parent_have_bounded_shutdown(self):
        process = Mock(pid=4242)
        with patch.object(review_preview.os, "killpg") as killpg, \
             patch.object(review_preview.time, "monotonic", side_effect=[0, 2]):
            _stop_browser(process, None)
        assert killpg.call_args_list == [call(4242, signal.SIGTERM), call(4242, 0), call(4242, signal.SIGKILL)]

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
                assert launch.call_args.kwargs['start_new_session'] == (os.name == 'posix')
                if failure is not None:
                    raise failure
        return stop

    def test_body_failure_stays_primary_when_profile_cleanup_fails(self, tmp_path):
        original = AssertionError("CSP assertion failed")
        directory = str(tmp_path)
        with pytest.raises(AssertionError) as caught:
            self.run_context(Path(directory), original)
        assert caught.value is original
        assert 'Browser cleanup failed' in original.__notes__[0]
        assert 'still busy' in original.__notes__[0]

    def test_cleanup_failure_fails_an_otherwise_successful_context(self, tmp_path):
        directory = str(tmp_path)
        with pytest.raises(OSError, match='still busy') as caught:
            self.run_context(Path(directory))
        assert 'Browser cleanup failed' in caught.value.__notes__[0]

    @contextmanager
    def fake_startup(self, profile, ready_at):
        state = SimpleNamespace(elapsed=0)
        response = io.BytesIO(json.dumps([{"type": "page", "url": "about:blank",
                                          "webSocketDebuggerUrl": "ws://localhost/page/test"}]).encode())
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)

        def sleep(seconds):
            state.elapsed += seconds
            if ready_at is not None and state.elapsed >= ready_at:
                (profile / "DevToolsActivePort").write_text("1234\n/browser/test")

        clock = SimpleNamespace(monotonic=lambda: state.elapsed, sleep=sleep)
        with patch.object(review_preview, "time", clock), \
             patch.object(review_preview.tempfile, "mkdtemp", return_value=str(profile)), \
             patch.object(review_preview, "find_browser", return_value="chrome"), \
             patch.object(review_preview.subprocess, "Popen", return_value=Mock(poll=Mock(return_value=None))) as launch, \
             patch.object(review_preview.urllib.request, "build_opener", return_value=opener), \
             patch.object(review_preview.websocket, "create_connection") as connect, \
             patch.object(review_preview, "_stop_browser") as stop:
            state.launch, state.connect, state.stop = launch, connect, stop
            yield state

    def test_cold_browser_becoming_ready_after_ten_seconds_launches_once(self, tmp_path):
        directory = str(tmp_path)
        profile = Path(directory)
        with self.fake_startup(profile, ready_at=12) as state:
            with browser() as client:
                assert isinstance(client, DevTools)
                assert client.connection is state.connect.return_value
            state.launch.assert_called_once()
            state.stop.assert_called_once_with(state.launch.return_value, state.connect.return_value)
            assert state.elapsed >= 12
            assert state.elapsed < 12.1
            assert not profile.exists()

    def test_browser_that_never_becomes_ready_has_bounded_startup_and_cleanup(self, tmp_path):
        directory = str(tmp_path)
        profile = Path(directory)
        with self.fake_startup(profile, ready_at=None) as state:
            with pytest.raises(RuntimeError, match="startup timed out after 30s"):
                with browser():
                    pytest.fail("Browser without a DevTools port cannot become ready")
            assert state.elapsed >= 30
            assert state.elapsed < 30.1
            state.launch.assert_called_once()
            state.connect.assert_not_called()
            state.stop.assert_called_once_with(state.launch.return_value, None)
            assert not profile.exists()

    def test_browser_exit_reports_executable_return_code_and_output(self, tmp_path):
        directory = str(tmp_path)
        with patch.object(review_preview.tempfile, 'mkdtemp', return_value=directory), patch.object(review_preview, 'find_browser', return_value='/usr/bin/google-chrome'), patch.object(review_preview.subprocess, 'Popen') as launch, patch.object(review_preview, '_stop_browser') as stop, patch.object(review_preview, '_remove_profile') as remove, patch.object(review_preview.time, 'monotonic', return_value=0), patch.object(review_preview.time, 'sleep') as pause:
            def exited(*args, **kwargs):
                kwargs["stderr"].write(b"browser startup diagnostic")
                return Mock(poll=Mock(return_value=23))
            launch.side_effect = exited
            with pytest.raises(RuntimeError, match='/usr/bin/google-chrome.*exit code 23.*browser startup diagnostic'):
                with browser():
                    pytest.fail('Exited browser cannot become ready')
            launch.assert_called_once()
            pause.assert_not_called()
            stop.assert_called_once()
            remove.assert_called_once_with(directory)

    def test_startup_failure_stays_primary_and_cleanup_is_attempted(self, tmp_path):
        original = FileNotFoundError("chrome missing")
        directory = str(tmp_path)
        with patch.object(review_preview.tempfile, 'mkdtemp', return_value=directory), patch.object(review_preview, 'find_browser', side_effect=original), patch.object(review_preview, '_remove_profile', side_effect=OSError(errno.ENOTEMPTY, 'still busy')) as remove:
            with pytest.raises(FileNotFoundError) as caught:
                with browser():
                    pytest.fail('Startup must fail before yielding')
        assert caught.value is original
        remove.assert_called_once_with(directory)
        assert 'Browser cleanup failed' in original.__notes__[0]
