import os
import unittest
from unittest import mock

from stackchan_server.config import (
    dance_keyword_enabled,
    dance_mcp_enabled,
    dance_trigger_modes,
)


class DanceTriggerConfigTest(unittest.TestCase):
    def _with(self, value):
        env = {} if value is None else {"XZ_DANCE_TRIGGER": value}
        ctx = mock.patch.dict(os.environ, env, clear=False)
        ctx.start()
        if value is None:
            os.environ.pop("XZ_DANCE_TRIGGER", None)
        self.addCleanup(ctx.stop)

    def test_defaults_to_keyword_only(self):
        self._with(None)
        self.assertEqual(dance_trigger_modes(), {"keyword"})
        self.assertTrue(dance_keyword_enabled())
        self.assertFalse(dance_mcp_enabled())

    def test_both_modes(self):
        self._with("keyword,mcp")
        self.assertEqual(dance_trigger_modes(), {"keyword", "mcp"})
        self.assertTrue(dance_keyword_enabled())
        self.assertTrue(dance_mcp_enabled())

    def test_mcp_only(self):
        self._with("mcp")
        self.assertFalse(dance_keyword_enabled())
        self.assertTrue(dance_mcp_enabled())

    def test_tolerates_spaces_case_and_unknown_modes(self):
        self._with("  Keyword , bogus , MCP ")
        self.assertEqual(dance_trigger_modes(), {"keyword", "mcp"})

    def test_empty_disables_everything(self):
        self._with("")
        self.assertEqual(dance_trigger_modes(), set())
        self.assertFalse(dance_keyword_enabled())
        self.assertFalse(dance_mcp_enabled())


if __name__ == "__main__":
    unittest.main()
