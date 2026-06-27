import unittest

from stackchan_server.stackchan.commands import CommandRouter


class CommandRouterTest(unittest.TestCase):
    def test_resolves_voice_text_to_supported_device_commands(self):
        router = CommandRouter()

        self.assertEqual(router.resolve_text("来跳个舞吧").action, "dance")
        self.assertEqual(router.resolve_text("唱首歌").action, "sing")
        self.assertEqual(router.resolve_text("停止动作").action, "stop")
        self.assertIsNone(router.resolve_text("今天天气怎么样"))

    def test_wake_word_only_text_is_not_a_device_command(self):
        router = CommandRouter()

        self.assertTrue(router.is_wake_word_only("你好小智"))
        self.assertTrue(router.is_wake_word_only("小智！"))
        self.assertTrue(router.is_wake_word_only("hey stack chan"))
        self.assertFalse(router.is_wake_word_only("小智，跳个舞"))
        self.assertIsNone(router.resolve_text("你好小智"))

    def test_queues_commands_per_device_and_poll_consumes_once(self):
        router = CommandRouter()

        queued = router.enqueue(device_id="stackchan-1", action="dance", source="test")

        self.assertEqual(queued.sequence, 1)
        self.assertEqual(queued.action, "dance")
        self.assertEqual(router.poll_next("stackchan-1").command_id, queued.command_id)
        self.assertIsNone(router.poll_next("stackchan-1"))


if __name__ == "__main__":
    unittest.main()
