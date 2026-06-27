import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from stackchan_server.stackchan.commands import CommandRouter
from stackchan_server.config import load_environment
from stackchan_server.apps.native_agent_api import ServerDependencies, create_app


class FakeAgent:
    async def start(self, channel_name, agent_uid, user_uid, output_audio_codec=None):
        return {
            "agent_id": f"fake-{agent_uid}",
            "channel_name": channel_name,
            "status": "started",
            "codec": output_audio_codec,
        }

    async def stop(self, agent_id):
        if not agent_id:
            raise ValueError("agent_id is required")


class ServerFactoryObservabilityTest(unittest.TestCase):
    def test_load_environment_reads_server_root_env_files_after_package_move(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env.local").write_text("STACKCHAN_TEST_ENV=from-local\n", encoding="utf-8")
            original = os.environ.pop("STACKCHAN_TEST_ENV", None)
            try:
                loaded = load_environment(root)
                self.assertEqual(os.environ["STACKCHAN_TEST_ENV"], "from-local")
                self.assertEqual(loaded.loaded_files, (root / ".env.local",))
            finally:
                os.environ.pop("STACKCHAN_TEST_ENV", None)
                if original is not None:
                    os.environ["STACKCHAN_TEST_ENV"] = original

    def test_create_app_supports_dependency_injection_for_reusable_http_tests(self):
        token_calls = []

        def fake_token_generator(**kwargs):
            token_calls.append(kwargs)
            return "fake-token"

        app = create_app(
            ServerDependencies(
                agent=FakeAgent(),
                command_router=CommandRouter(),
                token_generator=fake_token_generator,
                app_id="fake-app-id",
                app_certificate="fake-cert",
            )
        )
        client = TestClient(app)

        response = client.get("/get_config?channel=stackchan-poc&uid=10001")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["token"], "fake-token")
        self.assertEqual(token_calls[0]["app_id"], "fake-app-id")
        self.assertEqual(token_calls[0]["uid"], 10001)

    def test_request_id_health_and_metrics_make_api_observable(self):
        app = create_app(
            ServerDependencies(
                agent=FakeAgent(),
                command_router=CommandRouter(),
                token_generator=lambda **_: "fake-token",
                app_id="fake-app-id",
                app_certificate="fake-cert",
            )
        )
        client = TestClient(app)

        response = client.post(
            "/device/commands",
            json={"deviceId": "stackchan-1", "action": "dance", "source": "test"},
            headers={"x-request-id": "req-test-1"},
        )
        health = client.get("/healthz")
        metrics = client.get("/metrics")

        self.assertEqual(response.headers["x-request-id"], "req-test-1")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ok"])
        self.assertTrue(health.json()["dependencies"]["agent_configured"])
        self.assertGreaterEqual(metrics.json()["requests_total"], 3)
        self.assertEqual(metrics.json()["commands_queued_total"], 1)


if __name__ == "__main__":
    unittest.main()
