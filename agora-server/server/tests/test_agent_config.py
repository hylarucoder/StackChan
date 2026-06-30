import os
import unittest

os.environ.setdefault("AGORA_APP_ID", "test-app-id")
os.environ.setdefault("AGORA_APP_CERTIFICATE", "test-app-certificate")

from stackchan_server.agora.agent import Agent, AgentSettings, ADA_PROMPT, DEFAULT_AGENT_PRESET


class AgentConfigTest(unittest.TestCase):
    def test_agent_accepts_explicit_settings_and_client_factory(self):
        calls = []

        class FakeClient:
            pass

        def fake_client_factory(**kwargs):
            calls.append(kwargs)
            return FakeClient()

        settings = AgentSettings(
            app_id="explicit-app-id",
            app_certificate="explicit-cert",
            customer_id="customer-id",
            customer_secret="customer-secret",
            greeting="",
            idle_timeout=450,
            stt_language="zh-CN",
            agent_preset=DEFAULT_AGENT_PRESET,
            tts_voice_id="voice-id",
        )

        agent = Agent(settings=settings, client_factory=fake_client_factory)

        self.assertIsInstance(agent.client, FakeClient)
        self.assertEqual(agent.idle_timeout, 450)
        self.assertEqual(calls[0]["app_id"], "explicit-app-id")
        self.assertEqual(calls[0]["customer_id"], "customer-id")

    def test_invalid_idle_timeout_env_falls_back_to_safe_default(self):
        original = os.environ.get("AGENT_IDLE_TIMEOUT")
        os.environ["AGENT_IDLE_TIMEOUT"] = "not-a-number"
        try:
            settings = AgentSettings.from_environment(
                app_id="test-app-id",
                app_certificate="test-app-certificate",
            )
        finally:
            if original is None:
                os.environ.pop("AGENT_IDLE_TIMEOUT", None)
            else:
                os.environ["AGENT_IDLE_TIMEOUT"] = original

        self.assertEqual(settings.idle_timeout, 300)

    def test_default_greeting_is_stackchan_intro(self):
        original = os.environ.pop("AGENT_GREETING", None)
        try:
            agent = Agent()
        finally:
            if original is not None:
                os.environ["AGENT_GREETING"] = original

        self.assertEqual(agent.greeting, "Hi，我是 StackChan")

    def test_default_idle_timeout_keeps_multi_turn_session_open(self):
        original = os.environ.pop("AGENT_IDLE_TIMEOUT", None)
        try:
            agent = Agent()
        finally:
            if original is not None:
                os.environ["AGENT_IDLE_TIMEOUT"] = original

        self.assertGreaterEqual(agent.idle_timeout, 300)

    def test_start_request_keeps_managed_pipeline_and_chinese_turn_detection(self):
        originals = {
            "AGENT_PRESET": os.environ.pop("AGENT_PRESET", None),
            "STT_LANGUAGE": os.environ.pop("STT_LANGUAGE", None),
            "TTS_VOICE_ID": os.environ.pop("TTS_VOICE_ID", None),
            "AGENT_GREETING": os.environ.pop("AGENT_GREETING", None),
        }
        try:
            agent = Agent()
        finally:
            for key, value in originals.items():
                if value is not None:
                    os.environ[key] = value

        properties, preset = agent._build_start_request(
            channel_name="test-channel",
            agent_uid=1234,
            user_uid=5678,
            output_audio_codec="opus",
        )

        self.assertEqual(preset, DEFAULT_AGENT_PRESET)
        # turn_detection must set mode="default" (activates the detailed config) and must
        # NOT carry a bogus "language" key (language belongs in asr.params, asserted next).
        self.assertEqual(properties["turn_detection"]["mode"], "default")
        self.assertNotIn("language", properties["turn_detection"])
        self.assertEqual(
            properties["turn_detection"]["config"]["end_of_speech"]["vad_config"][
                "silence_duration_ms"
            ],
            480,
        )
        self.assertEqual(properties["asr"]["params"]["language"], "zh-CN")
        self.assertIn("system_messages", properties["llm"])
        self.assertEqual(properties["tts"]["params"]["voice_setting"]["voice_id"], "Chinese (Mandarin)_Warm_Girl")
        self.assertIn("minimax_speech_2_8_turbo", preset)
        self.assertEqual(properties["parameters"]["output_audio_codec"], "opus")

    def test_prompt_has_no_parrot_prone_meta_instructions(self):
        # gpt-4o-mini reads silence/again meta-instructions out loud verbatim
        # (e.g. it spoke "请保持安静" and "我在"). The persona prompt must not
        # contain those phrases; turn-taking/echo is handled outside the prompt.
        for phrase in ("我在", "保持安静", "什么都不要说", "不要重复"):
            self.assertNotIn(phrase, ADA_PROMPT)
        self.assertIn("Stack Chan", ADA_PROMPT)


if __name__ == "__main__":
    unittest.main()
