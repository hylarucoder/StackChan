import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from stackchan_server.stackchan.dance_channel import parse_frame
from stackchan_server.xiaozhi import app as xz_app

SAMPLE = [
    {"yawServo": {"angle": 0, "speed": 200}, "pitchServo": {"angle": 0, "speed": 200},
     "mouth": {"weight": 0}, "durationMs": 400},
    {"yawServo": {"angle": 300, "speed": 600}, "pitchServo": {"angle": -40, "speed": 600},
     "mouth": {"weight": 80}, "durationMs": 500},
    {"yawServo": {"angle": -300, "speed": 600}, "pitchServo": {"angle": -40, "speed": 600},
     "mouth": {"weight": 80}, "durationMs": 500},
]


class DancePushEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(xz_app.app)

    def _use_temp_dance_json(self):
        tmp = tempfile.TemporaryDirectory()
        old_path = xz_app.CURRENT_DANCE_PATH
        xz_app.CURRENT_DANCE_PATH = Path(tmp.name) / "dance.json"
        self.addCleanup(lambda: setattr(xz_app, "CURRENT_DANCE_PATH", old_path))
        self.addCleanup(tmp.cleanup)
        return xz_app.CURRENT_DANCE_PATH

    def test_push_inline_sequence_reaches_connected_device(self):
        self._use_temp_dance_json()
        with self.client.websocket_connect("/dance/ws?deviceId=dev-test") as ws:
            # Device shows up as a dance channel.
            health = self.client.get("/xiaozhi/healthz").json()
            self.assertIn("dev-test", health["dance_channels"])

            resp = self.client.post(
                "/trigger/dance/push",
                json={"deviceId": "dev-test", "sequence": SAMPLE},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["keyframes"], len(SAMPLE))

            frame = ws.receive_bytes()
            msg_type, payload = parse_frame(frame)
            self.assertEqual(msg_type, 0x14)
            self.assertEqual(json.loads(payload), SAMPLE)

        # After disconnect the channel is gone.
        self.assertNotIn("dev-test", self.client.get("/xiaozhi/healthz").json()["dance_channels"])

    def test_push_matches_device_id_case_insensitively(self):
        self._use_temp_dance_json()
        with self.client.websocket_connect("/dance/ws?deviceId=stackchan-1") as ws:
            resp = self.client.post(
                "/trigger/dance/push",
                json={"deviceId": "stackchan-1", "sequence": SAMPLE},
            )
            self.assertEqual(resp.status_code, 200, resp.text)

            frame = ws.receive_bytes()
            msg_type, payload = parse_frame(frame)
            self.assertEqual(msg_type, 0x14)
            self.assertEqual(json.loads(payload), SAMPLE)

    def test_push_overwrites_single_disk_dance_json_before_sending(self):
        dance_json = self._use_temp_dance_json()
        with self.client.websocket_connect("/dance/ws?deviceId=dev-write") as ws:
            resp = self.client.post(
                "/trigger/dance/push",
                json={"deviceId": "dev-write", "sequence": SAMPLE},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(json.loads(dance_json.read_text(encoding="utf-8")), SAMPLE)

            frame = ws.receive_bytes()
            msg_type, payload = parse_frame(frame)
            self.assertEqual(msg_type, 0x14)
            self.assertEqual(json.loads(payload), SAMPLE)

    def test_trigger_dance_pushes_single_disk_dance_json(self):
        dance_json = self._use_temp_dance_json()
        dance_json.write_text(json.dumps(SAMPLE), encoding="utf-8")
        with self.client.websocket_connect("/dance/ws?deviceId=dev-default") as ws:
            resp = self.client.post("/trigger/dance?device=dev-default&style=happy")
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["device"], "dev-default")
            self.assertGreater(body["result"]["keyframes"], 0)

            frame = ws.receive_bytes()
            msg_type, payload = parse_frame(frame)
            self.assertEqual(msg_type, 0x14)
            self.assertEqual(json.loads(payload), SAMPLE)

    def test_push_to_unconnected_device_is_404(self):
        resp = self.client.post("/trigger/dance/push",
                                json={"deviceId": "ghost", "sequence": SAMPLE})
        self.assertEqual(resp.status_code, 404)

    def test_push_with_no_channels_is_409(self):
        # Ensure registry is empty for this assertion.
        for d in list(xz_app.DANCE.device_ids()):
            xz_app.DANCE.unregister(d, xz_app.DANCE.get(d))
        resp = self.client.post("/trigger/dance/push", json={"sequence": SAMPLE})
        self.assertEqual(resp.status_code, 409)

    def test_push_invalid_keyframe_is_400(self):
        # A list (passes Pydantic) but a keyframe missing durationMs -> our validation rejects it.
        with self.client.websocket_connect("/dance/ws?deviceId=dev-bad"):
            resp = self.client.post(
                "/trigger/dance/push",
                json={"deviceId": "dev-bad", "sequence": [{"yawServo": {}, "pitchServo": {}}]},
            )
            self.assertEqual(resp.status_code, 400)


class _FakeVoice:
    def __init__(self, events: list[str]):
        self.events = events

    async def stop(self):
        self.events.append("voice-stopped")


class _FakeSession:
    def __init__(self, voice):
        self.session_id = "session-dance-voice"
        self.voice = voice


class _FakeDanceWs:
    def __init__(self, events: list[str]):
        self.events = events

    async def send_bytes(self, data: bytes) -> None:
        self.events.append("dance-pushed")


class DanceVoiceCoordinationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = xz_app.CURRENT_DANCE_PATH
        xz_app.CURRENT_DANCE_PATH = Path(self.tmp.name) / "dance.json"
        xz_app.CURRENT_DANCE_PATH.write_text(json.dumps(SAMPLE), encoding="utf-8")

    def tearDown(self):
        xz_app.CURRENT_DANCE_PATH = self.old_path
        xz_app.SESSIONS.pop("dev-dance-voice", None)
        ws = xz_app.DANCE.get("dev-dance-voice")
        if ws is not None:
            xz_app.DANCE.unregister("dev-dance-voice", ws)
        self.tmp.cleanup()

    async def test_dance_push_stops_active_voice_before_sending_motion(self):
        events: list[str] = []
        voice = _FakeVoice(events)
        xz_app.SESSIONS["dev-dance-voice"] = _FakeSession(voice)
        xz_app.DANCE.register("dev-dance-voice", _FakeDanceWs(events))

        result = await xz_app._push_default_dance("dev-dance-voice", "happy")

        self.assertTrue(result["sent"])
        self.assertEqual(events, ["voice-stopped", "dance-pushed"])
        self.assertIsNone(xz_app.SESSIONS["dev-dance-voice"].voice)


if __name__ == "__main__":
    unittest.main()
