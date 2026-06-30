import asyncio
import base64
import json
import os
import unittest
from unittest import mock

from stackchan_server.xiaozhi.voice_bridge import VoiceBridge


def transcript_frame(who: str, text: str) -> bytes:
    payload = json.dumps({"object": who, "text": text}).encode("utf-8")
    return b"msg-1|0|1|" + base64.b64encode(payload)


class _FakeSession:
    def __init__(self):
        self.dances: list[str] = []

    async def trigger_dance(self, style: str = "happy"):
        self.dances.append(style)
        return {"sent": True}


class VoiceDanceTriggerTest(unittest.IsolatedAsyncioTestCase):
    async def test_user_passphrase_triggers_dance_push_path(self):
        session = _FakeSession()
        bridge = VoiceBridge(session, asyncio.get_running_loop())

        bridge._handle_transcript(transcript_frame("user.transcription", "接着奏乐接着舞"))
        await asyncio.sleep(0)

        self.assertEqual(session.dances, ["happy"])

    async def test_assistant_echo_does_not_trigger_dance(self):
        session = _FakeSession()
        bridge = VoiceBridge(session, asyncio.get_running_loop())

        bridge._handle_transcript(transcript_frame("assistant.transcription", "接着奏乐接着舞"))
        await asyncio.sleep(0)

        self.assertEqual(session.dances, [])

    async def test_keyword_mode_off_does_not_trigger_dance(self):
        # XZ_DANCE_TRIGGER without "keyword" (e.g. MCP-only): the matcher must stay silent.
        session = _FakeSession()
        bridge = VoiceBridge(session, asyncio.get_running_loop())

        with mock.patch.dict(os.environ, {"XZ_DANCE_TRIGGER": "mcp"}):
            bridge._handle_transcript(transcript_frame("user.transcription", "接着奏乐接着舞"))
            await asyncio.sleep(0)

        self.assertEqual(session.dances, [])


if __name__ == "__main__":
    unittest.main()
