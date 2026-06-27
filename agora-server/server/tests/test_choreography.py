import json
import unittest

import numpy as np

from stackchan_server.stackchan.choreography.analysis import AudioFeatures
from stackchan_server.stackchan.choreography.compiler import CompileOptions, compile_from_features
from stackchan_server.stackchan.choreography.schema import (
    Feature,
    Keyframe,
    KeyframeSequence,
    MotionLimits,
    Servo,
)


def _fake_features(n_beats: int = 16, bpm: float = 120.0) -> AudioFeatures:
    """Synthetic beat grid so the compiler is testable without librosa/audio."""
    period = 60.0 / bpm
    beat_times = np.arange(n_beats) * period
    # Ramp energy quiet -> loud so all three sections appear.
    beat_energy = np.linspace(0.1, 0.95, n_beats)
    sections = ["quiet"] * (n_beats // 3) + ["mid"] * (n_beats // 3)
    sections += ["loud"] * (n_beats - len(sections))
    return AudioFeatures(
        path="fake.mp3",
        sr=22050,
        duration_s=n_beats * period,
        tempo_bpm=bpm,
        beat_times=beat_times,
        beat_energy=beat_energy,
        beats_per_bar=4,
        section_of_beat=sections,
        sustain_of_beat=[False] * n_beats,
    )


class SchemaTest(unittest.TestCase):
    def test_motion_limits_clamp_to_safe_band(self):
        limits = MotionLimits(yaw_max=600, pitch_min=-300, pitch_max=200)
        self.assertEqual(limits.clamp_yaw(9999), 600)
        self.assertEqual(limits.clamp_yaw(-9999), -600)
        self.assertEqual(limits.clamp_pitch(9999), 200)
        self.assertEqual(limits.clamp_pitch(-9999), -300)
        self.assertEqual(limits.clamp_speed(99999), 1000)
        self.assertEqual(limits.clamp_speed(0), 80)

    def test_yaw_max_never_exceeds_physical_ceiling(self):
        limits = MotionLimits(yaw_max=5000)  # caller asks for more than hardware
        self.assertLessEqual(limits.clamp_yaw(5000), 1280)

    def test_feature_weight_is_clamped_in_serialization(self):
        kf = Keyframe(mouth=Feature(weight=999), yawServo=Servo(0, 200), pitchServo=Servo(0, 200))
        self.assertEqual(kf.to_dict()["mouth"]["weight"], 100)

    def test_color_omitted_when_unset(self):
        kf = Keyframe()
        self.assertNotIn("leftRgbColor", kf.to_dict())
        kf.leftRgbColor = "#FF0000"
        self.assertEqual(kf.to_dict()["leftRgbColor"], "#FF0000")

    def test_sequence_json_is_an_array(self):
        seq = KeyframeSequence([Keyframe(durationMs=500)])
        data = json.loads(seq.to_json())
        self.assertIsInstance(data, list)
        self.assertEqual(seq.total_ms(), 500)


class CompilerTest(unittest.TestCase):
    def setUp(self):
        self.features = _fake_features()
        self.choreo = compile_from_features(self.features, CompileOptions())
        self.data = json.loads(self.choreo.sequence.to_json())

    def test_every_keyframe_matches_firmware_schema(self):
        required = {"leftEye", "rightEye", "mouth", "yawServo", "pitchServo", "durationMs"}
        for kf in self.data:
            self.assertTrue(required.issubset(kf.keys()))
            self.assertIn("angle", kf["yawServo"])
            self.assertIn("speed", kf["yawServo"])

    def test_has_intro_ritual_groove_and_closing_home(self):
        # opening ritual at the front, groove moves in the middle, home at the end
        import math

        from stackchan_server.stackchan.choreography import moves
        from stackchan_server.stackchan.choreography.schema import MotionLimits
        intro = moves.intro_keyframes(MotionLimits())
        seq = self.choreo.sequence
        # front matches the ritual (centered yaw, look-up then nod pattern)
        self.assertGreater(len(seq), len(intro) + 1)
        self.assertEqual(seq[0].yawServo.angle, 0)  # ritual starts centered
        # closing pose is home (centered)
        self.assertEqual(seq[-1].yawServo.angle, 0)
        self.assertEqual(seq[-1].pitchServo.angle, 0)
        # groove move count never exceeds one-per-(step) beats
        step = CompileOptions().beats_per_move
        max_moves = math.ceil(self.features.n_beats / step)
        self.assertLessEqual(len(seq) - len(intro) - 1, max_moves)

    def test_beats_per_move_controls_density(self):
        dense = compile_from_features(self.features, CompileOptions(beats_per_move=1, framing=False))
        calm = compile_from_features(self.features, CompileOptions(beats_per_move=4, framing=False))
        self.assertEqual(len(dense.sequence), self.features.n_beats)
        self.assertLess(len(calm.sequence), len(dense.sequence))

    def test_all_angles_within_physical_limits(self):
        for kf in self.data:
            self.assertLessEqual(abs(kf["yawServo"]["angle"]), 1280)
            self.assertTrue(-400 <= kf["pitchServo"]["angle"] <= 870)
            for servo in ("yawServo", "pitchServo"):
                self.assertTrue(0 <= kf[servo]["speed"] <= 1000)

    def test_durations_are_positive_and_respect_minimum(self):
        body = self.data[1:-1]  # exclude framing poses
        for kf in body:
            self.assertGreaterEqual(kf["durationMs"], CompileOptions().min_duration_ms)

    def test_framing_can_be_disabled(self):
        seq = compile_from_features(
            self.features, CompileOptions(framing=False, beats_per_move=1)).sequence
        self.assertEqual(len(seq), self.features.n_beats)

    def test_color_disabled_omits_rgb(self):
        seq = compile_from_features(self.features, CompileOptions(color_bars=False)).sequence
        for kf in seq.to_list():
            self.assertNotIn("leftRgbColor", kf)

    def test_summary_reports_tempo_and_counts(self):
        summary = self.choreo.summary()
        self.assertIn("BPM", summary)
        self.assertIn("keyframes=", summary)


if __name__ == "__main__":
    unittest.main()
