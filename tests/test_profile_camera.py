"""Check the standalone comparison's lifecycle schedule and cleanup verdicts."""

import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("profile_camera", SCRIPTS / "profile_camera.py")
profiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiler)


class ProfileCameraTests(unittest.TestCase):
    def test_schedule_exercises_stop_restart_hide_return_and_ten_rapid_cycles(self):
        expected = {0: "play", 240: "stop", 260: "play", 380: "hide", 410: "show",
                    470: "play", 580: "stop", 600: "play"}
        for seconds, action in expected.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(profiler.phase_at(seconds, 600)[1], action)
                self.assertEqual(profiler.phase_at(seconds / 10, 60)[1], action)
        for step in range(20):
            self.assertEqual(profiler.phase_at(430 + step * 2, 600),
                             (f"rapid-{step}", "play" if step % 2 == 0 else "stop"))

    def test_returning_must_remain_stopped_with_no_frame_or_url(self):
        clean = {"hidden": False, "playing": False, "sourcePresent": False, "liveURLs": 0}
        profiler.check_state(clean, "show")
        for field, value in (("playing", True), ("sourcePresent", True), ("liveURLs", 1)):
            with self.subTest(field=field), self.assertRaises(AssertionError):
                profiler.check_state(dict(clean, **{field: value}), "show")

    def test_playback_allows_only_one_pending_blob(self):
        playing = {"hidden": False, "playing": True, "sourcePresent": True, "liveURLs": 1}
        profiler.check_state(playing, "play")
        with self.assertRaises(AssertionError):
            profiler.check_state(dict(playing, liveURLs=2), "play")
