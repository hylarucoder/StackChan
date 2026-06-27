import json
import unittest

from stackchan_server.stackchan.dance_channel import (
    DANCE,
    DanceRegistry,
    frame_dance,
    load_dance_payload,
    parse_frame,
    sequence_to_payload,
    validate_sequence,
)

SAMPLE = [
    {"yawServo": {"angle": 0, "speed": 200}, "pitchServo": {"angle": 0, "speed": 200},
     "mouth": {"weight": 0}, "durationMs": 400},
    {"yawServo": {"angle": 300, "speed": 600}, "pitchServo": {"angle": -40, "speed": 600},
     "mouth": {"weight": 80}, "durationMs": 500, "leftRgbColor": "#FF0000"},
]


class FrameTest(unittest.TestCase):
    def test_frame_has_type_and_big_endian_length(self):
        frame = frame_dance(b"hello")
        self.assertEqual(frame[0], DANCE)
        self.assertEqual(frame[0], 0x14)
        self.assertEqual(frame[1:5], (5).to_bytes(4, "big"))
        self.assertEqual(frame[5:], b"hello")

    def test_frame_parse_round_trip(self):
        payload = b'[{"a":1}]'
        msg_type, decoded = parse_frame(frame_dance(payload))
        self.assertEqual(msg_type, DANCE)
        self.assertEqual(decoded, payload)

    def test_parse_rejects_short_and_mismatched_frames(self):
        with self.assertRaises(ValueError):
            parse_frame(b"\x14\x00")
        bad = bytes([DANCE]) + (99).to_bytes(4, "big") + b"short"
        with self.assertRaises(ValueError):
            parse_frame(bad)


class ValidateTest(unittest.TestCase):
    def test_accepts_keyframe_array(self):
        self.assertEqual(validate_sequence(SAMPLE), SAMPLE)

    def test_rejects_non_array(self):
        with self.assertRaises(ValueError):
            validate_sequence({"not": "a list"})

    def test_rejects_keyframe_missing_required_field(self):
        with self.assertRaises(ValueError):
            validate_sequence([{"yawServo": {}, "pitchServo": {}}])  # no durationMs

    def test_payload_is_compact_json(self):
        payload = sequence_to_payload(SAMPLE)
        self.assertNotIn(b", ", payload)  # compact separators
        self.assertEqual(json.loads(payload), SAMPLE)

    def test_load_dance_payload_from_file(self):
        import os
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".dance.json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(SAMPLE, f)
            payload = load_dance_payload(path)
            self.assertEqual(json.loads(payload), SAMPLE)
        finally:
            os.unlink(path)


class _FakeWs:
    def __init__(self):
        self.sent: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)


class RegistryTest(unittest.IsolatedAsyncioTestCase):
    def test_register_get_unregister(self):
        reg = DanceRegistry()
        ws = _FakeWs()
        reg.register("dev1", ws)
        self.assertIs(reg.get("dev1"), ws)
        self.assertEqual(reg.device_ids(), ["dev1"])
        reg.unregister("dev1", ws)
        self.assertIsNone(reg.get("dev1"))

    def test_unregister_only_drops_matching_connection(self):
        reg = DanceRegistry()
        old, new = _FakeWs(), _FakeWs()
        reg.register("dev1", old)
        reg.register("dev1", new)  # reconnect replaces
        reg.unregister("dev1", old)  # stale close must not drop the new one
        self.assertIs(reg.get("dev1"), new)

    async def test_push_payload_sends_framed_bytes(self):
        reg = DanceRegistry()
        ws = _FakeWs()
        reg.register("dev1", ws)
        payload = sequence_to_payload(SAMPLE)
        size = await reg.push_payload("dev1", payload)
        self.assertEqual(len(ws.sent), 1)
        msg_type, decoded = parse_frame(ws.sent[0])
        self.assertEqual(msg_type, DANCE)
        self.assertEqual(json.loads(decoded), SAMPLE)
        self.assertEqual(size, len(ws.sent[0]))

    async def test_push_to_unknown_device_raises(self):
        reg = DanceRegistry()
        with self.assertRaises(KeyError):
            await reg.push_payload("nope", b"[]")


if __name__ == "__main__":
    unittest.main()
