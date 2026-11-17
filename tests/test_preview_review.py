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


class TestPreviewDriver:
    @pytest.fixture
    def client(self, tmp_path):
        from prepare_screenshots import prepare, find_browser
        try:
            find_browser()
        except RuntimeError:
            pytest.skip('Chrome/Chromium is not installed')
        entry = prepare(tmp_path)
        with browser() as client:
            client.call("Emulation.setDeviceMetricsOverride", {"width": 1100, "height": 900,
                                                               "deviceScaleFactor": 1, "mobile": False})
            client.call("Page.navigate", {"url": entry.as_uri()})
            client.wait_for("Boolean(window.previewDriver && document.querySelector('#stats'))")
            client.evaluate("window.previewDriver.selectState('printing')")
            client.wait_for("Boolean(document.querySelector('.chosen .index-tab'))")
            yield client

    @pytest.mark.parametrize('fail', [False, True], ids=['success', 'assertion-failure'])
    def test_controlled_sample_survives_activity_and_restores_after_scope(self, client, fail):
        previous = client.evaluate("window.previewDriver.sample")
        expression = """window.previewDriver.withSamples(async update => {
          const sample = window.previewDriver.sample;
          sample.tools.forEach((tool, index) => { tool.state = index === 0 ? 'connected' : 'parked'; });
          await update(sample);
          window.dispatchEvent(new Event('pageshow'));
          document.querySelector('[role="switch"]').click();
          await new Promise(resolve => setTimeout(resolve, 1100));
          if (document.querySelector('.chosen .index-tab').dataset.tool !== '0')
            throw new Error('Activity replaced the controlled sample');
          await update({state:'unavailable'});
          if (document.querySelector('#hardware-stat').getClientRects().length)
            throw new Error('Hardware is visible without a sample');
          %s
        })""" % ("throw new Error('Injected assertion failure');" if fail else "return 'completed';")
        if fail:
            with pytest.raises(RuntimeError, match='Injected assertion failure'):
                client.evaluate(expression)
        else:
            assert client.evaluate(expression) == 'completed'
        assert client.evaluate("window.previewDriver.sample") == previous
        assert client.evaluate("document.querySelector('.chosen .index-tab').dataset.tool") == str(previous['tool'])
        # Observe delivery after checking the real UI, so heartbeat restoration cannot hide behind a stale DOM.
        client.evaluate("window.deliveries = []; window.orca.onMessage(message => window.deliveries.push(message))")
        client.evaluate("window.dispatchEvent(new Event('pageshow'))")
        assert client.evaluate("window.deliveries.find(message => message.stats)?.stats") == previous

    def test_nested_scope_restores_previous_sample_and_suspended_heartbeat(self, client):
        assert client.evaluate("""(async () => {
          const fixture = window.previewDriver.sample;
          const deliveries = [];
          window.orca.onMessage(message => deliveries.push(message));
          await window.previewDriver.withSamples(async update => {
            const outer = {...fixture, progress:.123};
            await update(outer);
            try {
              await window.previewDriver.withSamples(async update => {
                window.previewDriver.selectState('paused');
                await update({state:'unavailable'});
                throw new Error('inner assertion');
              });
            } catch (error) {
              if (error.message !== 'inner assertion') throw error;
            }
            if (JSON.stringify(window.previewDriver.sample) !== JSON.stringify(outer)) throw new Error('Previous sample lost');
            if (deliveries.filter(message => message.stats).at(-1).stats.progress !== outer.progress) throw new Error('Previous sample was not delivered');
            deliveries.length = 0;
            window.dispatchEvent(new Event('pageshow'));
            if (deliveries.length) throw new Error('Outer heartbeat resumed too early');
          });
          deliveries.length = 0;
          window.dispatchEvent(new Event('pageshow'));
          return JSON.stringify(deliveries.find(message => message.stats)?.stats) === JSON.stringify(fixture);
        })()""")

    @pytest.mark.parametrize('width', [1100, 390], ids=['desktop', 'narrow'])
    def test_idle_filament_colors_reveal_on_hover_and_focus_using_current_presence(self, client, width):
        client.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900,
                                                           "deviceScaleFactor": 1, "mobile": False})
        for state in ('complete', 'standby'):
            client.evaluate("window.previewDriver.selectState(" + json.dumps(state) + ")")
            client.evaluate("document.activeElement?.blur()")
            client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 0, "y": 0})
            client.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            result = client.evaluate("""window.previewDriver.withSamples(async update => {
              const sample = window.previewDriver.sample;
              sample.tools = [
                {state:'parked', filament_present:true, color:'#12AB34', material:'PETG'},
                {state:'parked', filament_present:false, color:'#FE1234', material:'old material'},
                {state:'unknown', color:'#1234FE', material:'unconfirmed material'},
                {state:'parked', filament_present:true, color:null}
              ];
              await update(sample);
              const palette = document.querySelector('.idle-filaments');
              if (!palette) throw new Error('Idle filament reveal is missing');
              const card = document.querySelector('#stats');
              const snapshot = () => ({
                visible:getComputedStyle(palette).visibility === 'visible',
                labels:[...palette.querySelectorAll('[role="img"]')].map(e => e.getAttribute('aria-label')),
                colors:[...palette.querySelectorAll('.swatch')].map(e => e.style.backgroundColor),
                card:JSON.stringify(card.getBoundingClientRect().toJSON()),
                heating:palette.querySelector('.heating,.temperature-arrow,.trace-shimmer') !== null
              });
              const before = snapshot();
              card.focus();
              await new Promise(resolve => requestAnimationFrame(resolve));
              const focused = snapshot();
              card.blur();
              await new Promise(resolve => setTimeout(resolve, 280));
              const after = snapshot();
              await update({...sample, tools:[]});
              const missing = [...document.querySelectorAll('.idle-filaments .swatch')].every(e => !e.style.backgroundColor);
              await update(sample);
              const now = Date.now;
              try {
                Date.now = () => now() + 11000;
                window.dispatchEvent(new Event('pageshow'));
                await new Promise(resolve => requestAnimationFrame(resolve));
              } finally { Date.now = now; }
              const stale = document.querySelector('.idle-filaments') === null;
              return {before, focused, after, missing, stale};
            })""")
            assert not result['before']['visible']
            assert result['focused']['visible']
            assert not result['after']['visible']
            assert result['before']['card'] == result['focused']['card'] == result['after']['card']
            assert not result['focused']['heating']
            assert result['missing'] and result['stale']
            labels = result['focused']['labels']
            assert len(labels) == 4
            assert '#12AB34' in labels[0] and 'PETG' in labels[0]
            assert 'No filament' in labels[1] and '#FE1234' not in labels[1]
            assert 'not reported' in labels[2] and '#1234FE' not in labels[2]
            assert 'Color not reported' in labels[3]
            assert 'rgb(18, 171, 52)' in result['focused']['colors']
            assert 'rgb(254, 18, 52)' not in result['focused']['colors']
            assert 'rgb(18, 52, 254)' not in result['focused']['colors']
            client.evaluate("document.querySelector('#stats').scrollIntoView({block:'nearest'})")
            point = client.evaluate("(() => {const r=document.querySelector('#stats').getBoundingClientRect();return {x:r.x+8,y:r.y+8}})()")
            client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **point})
            assert client.evaluate("getComputedStyle(document.querySelector('.idle-filaments')).visibility === 'visible'")
            assert client.evaluate("(() => {const r=document.querySelector('.idle-filaments').getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && r.top>=0})()")
            client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 0, "y": 0})
            client.evaluate("new Promise(resolve => setTimeout(resolve, 280))")
            assert not client.evaluate("getComputedStyle(document.querySelector('.idle-filaments')).visibility === 'visible'")

    @pytest.mark.parametrize('state', ['complete', 'standby'])
    def test_idle_palette_escape_survives_refresh_until_new_hover_or_focus(self, client, state):
        client.evaluate("window.previewDriver.selectState(" + json.dumps(state) + ")")
        visible = "getComputedStyle(document.querySelector('.idle-filaments')).visibility === 'visible'"
        refresh = "window.previewDriver.withSamples(async update => { await update(window.previewDriver.sample); })"

        def escape():
            client.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape"})
            client.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape"})

        client.evaluate("document.querySelector('#stats').focus()")
        client.wait_for(visible)
        escape()
        assert not client.evaluate(visible), 'Escape must dismiss the focused idle palette'
        client.evaluate(refresh)
        assert not client.evaluate(visible), 'Live refresh must preserve keyboard dismissal'
        client.evaluate("document.querySelector('#stats').blur(); document.querySelector('#stats').focus()")
        client.wait_for(visible)
        client.evaluate("document.querySelector('#stats').blur()")
        client.wait_for("!(" + visible + ")")
        client.evaluate("document.querySelector('#stats').scrollIntoView({block:'nearest'})")
        point = client.evaluate("(() => {const r=document.querySelector('#stats').getBoundingClientRect();return {x:r.x+8,y:r.y+8}})()")
        client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **point})
        client.wait_for(visible)
        escape()
        assert not client.evaluate(visible), 'Escape must dismiss the hovered idle palette'
        client.evaluate(refresh)
        client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": point['x'] + 1, "y": point['y']})
        assert not client.evaluate(visible), 'Refresh and movement inside the card must not reopen it'
        client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 0, "y": 0})
        client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **point})
        client.wait_for(visible)

    def test_real_dom_review_rejects_shifted_layout_and_restores_sample(self, client):
        previous = client.evaluate("window.previewDriver.sample")
        client.evaluate("document.querySelector('.current-temperature').style.transform = 'translateX(20px)'")
        with pytest.raises(ValueError, match='Trace does not meet bed surface|Nozzle temperature clearance'):
            review_preview.check_toolheads(client)
        assert client.evaluate("window.previewDriver.sample") == previous
        assert client.evaluate("document.querySelector('.chosen .index-tab').dataset.tool") == str(previous['tool'])


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
