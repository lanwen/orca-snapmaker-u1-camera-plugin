"""Check the standalone comparison's lifecycle schedule and cleanup verdicts."""

import importlib.util
from pathlib import Path
import sys
import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("profile_camera", SCRIPTS / "profile_camera.py")
profiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiler)


class TestProfileCamera:
    @pytest.mark.parametrize('seconds,action', [
        pytest.param(0, 'play', id='initial'),
        pytest.param(240, 'stop', id='stop'),
        pytest.param(260, 'play', id='restart'),
        pytest.param(380, 'hide', id='hide'),
        pytest.param(410, 'show', id='return'),
        pytest.param(470, 'play', id='post-cycles'),
        pytest.param(580, 'stop', id='final-stop'),
        pytest.param(600, 'play', id='next-cycle'),
    ])
    def test_schedule_exercises_stop_restart_hide_return_and_ten_rapid_cycles(self, seconds, action):
        assert profiler.phase_at(seconds, 600)[1] == action
        assert profiler.phase_at(seconds / 10, 60)[1] == action

    @pytest.mark.parametrize("step", range(20), ids=[f"rapid-{step}" for step in range(20)])
    def test_ten_rapid_playback_cycles(self, step):
        assert profiler.phase_at(430 + step * 2, 600) == (f"rapid-{step}", "play" if step % 2 == 0 else "stop")

    @pytest.mark.parametrize('field,value', [
        pytest.param('playing', True, id='playing'),
        pytest.param('sourcePresent', True, id='sourcePresent'),
        pytest.param('liveURLs', 1, id='liveURLs'),
    ])
    def test_returning_must_remain_stopped_with_no_frame_or_url(self, field, value):
        clean = {"hidden": False, "playing": False, "sourcePresent": False, "liveURLs": 0}
        profiler.check_state(clean, "show")
        with pytest.raises(AssertionError):
            profiler.check_state(dict(clean, **{field: value}), "show")

    def test_playback_allows_only_one_pending_blob(self):
        playing = {"hidden": False, "playing": True, "sourcePresent": True, "liveURLs": 1}
        profiler.check_state(playing, "play")
        with pytest.raises(AssertionError):
            profiler.check_state(dict(playing, liveURLs=2), "play")
