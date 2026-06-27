import os
import unittest

from fastapi.testclient import TestClient

os.environ.setdefault("AGORA_APP_ID", "test-app-id")
os.environ.setdefault("AGORA_APP_CERTIFICATE", "test-app-certificate")

from stackchan_server.stackchan.commands import CommandRouter
from stackchan_server.apps.native_agent_api import ServerDependencies, create_app


class FakeAgent:
    async def start(self, channel_name, agent_uid, user_uid, output_audio_codec=None):
        return {
            "agent_id": f"fake-agent-{agent_uid}",
            "channel_name": channel_name,
            "status": "started",
        }

    async def stop(self, agent_id):
        if not agent_id:
            raise ValueError("agent_id is required")


class DeviceCommandApiTest(unittest.TestCase):
    def setUp(self):
        self.token_calls = []

        def fake_token_generator(**kwargs):
            self.token_calls.append(kwargs)
            return "test-token"

        self.client = TestClient(
            create_app(
                ServerDependencies(
                    agent=FakeAgent(),
                    command_router=CommandRouter(),
                    token_generator=fake_token_generator,
                    app_id="test-app-id",
                    app_certificate="test-app-certificate",
                )
            )
        )

    def test_resolve_endpoint_enqueues_command_for_device_polling(self):
        response = self.client.post(
            "/device/commands/resolve",
            json={"deviceId": "stackchan-1", "text": "来跳个舞吧"},
        )
        payload = response.json()

        self.assertEqual(payload["code"], 0)
        self.assertTrue(payload["data"]["matched"])
        self.assertEqual(payload["data"]["command"]["action"], "dance")

        first_poll = self.client.get("/device/commands/next?deviceId=stackchan-1").json()
        self.assertTrue(first_poll["data"]["has_command"])
        self.assertEqual(first_poll["data"]["command"]["action"], "dance")

        second_poll = self.client.get("/device/commands/next?deviceId=stackchan-1").json()
        self.assertFalse(second_poll["data"]["has_command"])
        self.assertEqual(second_poll["data"]["command"]["action"], "idle")

    def test_get_config_uses_numeric_uid_token_generation(self):
        response = self.client.get("/get_config?channel=stackchan-poc&uid=10001")
        payload = response.json()

        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["token"], "test-token")
        self.assertEqual(payload["data"]["uid"], "10001")
        self.assertEqual(payload["data"]["channel_name"], "stackchan-poc")
        self.assertEqual(self.token_calls[0]["uid"], 10001)
        self.assertNotIn("account", self.token_calls[0])


if __name__ == "__main__":
    unittest.main()
