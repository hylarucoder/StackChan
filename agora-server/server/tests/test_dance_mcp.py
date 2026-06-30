import os
import unittest

os.environ.setdefault("AGORA_APP_ID", "test-app-id")
os.environ.setdefault("AGORA_APP_CERTIFICATE", "test-app-certificate")

from stackchan_server.agora.agent import Agent
from stackchan_server.xiaozhi import app as xzapp
from stackchan_server.xiaozhi.dance_mcp import build_dance_mcp


class DanceMcpToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_invokes_push_with_requested_style(self):
        calls: list[str] = []

        async def fake_push(style: str) -> dict:
            calls.append(style)
            return {"sent": True, "device": "dev-1"}

        mcp = build_dance_mcp(fake_push)
        await mcp.call_tool("dance", {"style": "excited"})
        self.assertEqual(calls, ["excited"])

    async def test_tool_defaults_style_to_happy(self):
        calls: list[str] = []

        async def fake_push(style: str) -> dict:
            calls.append(style)
            return {"sent": True, "device": "dev-1"}

        mcp = build_dance_mcp(fake_push)
        await mcp.call_tool("dance", {})
        self.assertEqual(calls, ["happy"])

    async def test_tool_does_not_raise_when_no_device(self):
        async def fake_push(style: str) -> dict:
            return {"sent": False, "reason": "no dance channel connected"}

        mcp = build_dance_mcp(fake_push)
        # The model must always get a clean tool result, even with nothing connected.
        await mcp.call_tool("dance", {"style": "happy"})


class McpPushDanceWiringTest(unittest.IsolatedAsyncioTestCase):
    async def test_push_reports_not_sent_when_no_dance_channel(self):
        # No /dance/ws channels registered in the module-level registry.
        result = await xzapp._mcp_push_dance("happy")
        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "no dance channel connected")


class DanceMcpServerRegistrationTest(unittest.TestCase):
    def _build(self, env: dict) -> dict:
        from unittest import mock
        with mock.patch.dict(os.environ, env, clear=False):
            for key in ("XZ_DANCE_TRIGGER", "XZ_DANCE_MCP_URL"):
                if key not in env:
                    os.environ.pop(key, None)
            properties, _ = Agent()._build_start_request(
                channel_name="c", agent_uid=1, user_uid=2,
            )
        return properties

    def test_mcp_server_registered_when_mode_on_and_url_set(self):
        properties = self._build({
            "XZ_DANCE_TRIGGER": "keyword,mcp",
            "XZ_DANCE_MCP_URL": "https://example.test/dance-mcp/mcp",
        })
        servers = properties["llm"]["mcp_servers"]
        self.assertEqual(servers[0]["url"], "https://example.test/dance-mcp/mcp")
        self.assertEqual(servers[0]["transport"], "streamable_http")
        self.assertTrue(properties["advanced_features"]["enable_tools"])

    def test_not_registered_when_mcp_mode_off_even_with_url(self):
        # Keyword-only trigger: the dance tool must not be registered.
        properties = self._build({
            "XZ_DANCE_TRIGGER": "keyword",
            "XZ_DANCE_MCP_URL": "https://example.test/dance-mcp/mcp",
        })
        self.assertNotIn("mcp_servers", properties["llm"])

    def test_not_registered_when_mcp_mode_on_but_url_unset(self):
        properties = self._build({"XZ_DANCE_TRIGGER": "mcp"})
        self.assertNotIn("mcp_servers", properties["llm"])


if __name__ == "__main__":
    unittest.main()
