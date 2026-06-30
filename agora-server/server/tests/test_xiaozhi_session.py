import unittest

from stackchan_server.xiaozhi.session import XzSession


class _FakeWs:
    def __init__(self):
        self.sent_text: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent_text.append(payload)


class XzSessionHelloTest(unittest.IsolatedAsyncioTestCase):
    async def test_hello_waits_for_before_response_hook_before_server_hello(self):
        events: list[str] = []
        session = XzSession(_FakeWs(), "dev-test")

        async def before_response():
            events.append("voice-ready")
            self.assertEqual(session.ws.sent_text, [])

        session.before_hello_response = before_response

        await session._on_hello({"audio_params": {"format": "opus"}})

        self.assertEqual(events, ["voice-ready"])
        self.assertEqual(len(session.ws.sent_text), 1)
        self.assertIn('"type": "hello"', session.ws.sent_text[0])
        self.assertTrue(session.hello_event.is_set())


if __name__ == "__main__":
    unittest.main()
