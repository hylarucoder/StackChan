"""
Agent

High-level API for managing Agora Conversational AI Agents.
"""
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from agora_agent import Area, AsyncAgora
from agora_agent.agentkit import AgentPresets
from agora_agent.agentkit.token import generate_convo_ai_token

logger = logging.getLogger("uvicorn.error")

# Keep this prompt a clean, positive persona only. Do NOT add meta-instructions
# like "stay silent", "don't repeat yourself", or 'reply "我在"': a small model
# (gpt-4o-mini) tends to read those out loud verbatim ("请保持安静") instead of
# obeying them. Turn-taking and echo are handled outside the prompt (firmware
# half-duplex + the engine's turn detection), not here.
ADA_PROMPT = """你是一只名叫「Stack Chan」的桌面小机器人，性格活泼、友善、爱卖萌，正在用中文和主人语音聊天。

- 用自然、口语化的普通话回答，简短亲切，通常一到两句话，除非主人明确要求详细解释。
- 语气轻松可爱，可以适当俏皮，但不浮夸。
- 直接回应主人这一句话的问题或请求；不要复述或解释这些规则，也不要描述你自己正在做什么。
- 不知道的事情就如实说不知道，不要编造。
- 不要使用 emoji，也不要把标点符号或括号里的内容念出来。
"""

DEFAULT_AGENT_PRESET = ",".join(
    (
        AgentPresets.asr.deepgram_nova_2,
        AgentPresets.llm.openai_gpt_4o_mini,
        AgentPresets.tts.minimax_speech_2_8_turbo,
    )
)
DEFAULT_STT_LANGUAGE = "zh-CN"
DEFAULT_TTS_VOICE_ID = "Chinese (Mandarin)_Warm_Girl"
DEFAULT_IDLE_TIMEOUT = 300


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer environment value %s=%r; using %s", name, raw, default)
        return default


@dataclass(frozen=True)
class AgentSettings:
    app_id: str
    app_certificate: str
    customer_id: Optional[str]
    customer_secret: Optional[str]
    greeting: str
    idle_timeout: int
    stt_language: str
    agent_preset: str
    tts_voice_id: str

    @classmethod
    def from_environment(
        cls,
        *,
        app_id: Optional[str] = None,
        app_certificate: Optional[str] = None,
    ) -> "AgentSettings":
        return cls(
            app_id=app_id or os.getenv("AGORA_APP_ID", ""),
            app_certificate=app_certificate or os.getenv("AGORA_APP_CERTIFICATE", ""),
            customer_id=os.getenv("AGORA_CUSTOMER_ID"),
            customer_secret=os.getenv("AGORA_CUSTOMER_SECRET"),
            greeting=os.getenv("AGENT_GREETING", ""),
            idle_timeout=_env_int("AGENT_IDLE_TIMEOUT", DEFAULT_IDLE_TIMEOUT),
            stt_language=os.getenv("STT_LANGUAGE", DEFAULT_STT_LANGUAGE),
            agent_preset=os.getenv("AGENT_PRESET", DEFAULT_AGENT_PRESET),
            tts_voice_id=os.getenv("TTS_VOICE_ID", DEFAULT_TTS_VOICE_ID),
        )


class Agent:
    """
    High-level client for Agora Conversational AI Agent operations.
    
    Uses AgentSession for full lifecycle management (start/stop),
    which handles Token007 authentication automatically.
    """
    
    def __init__(self, settings: Optional[AgentSettings] = None, client_factory=AsyncAgora):
        settings = settings or AgentSettings.from_environment()
        self.app_id = settings.app_id
        self.app_certificate = settings.app_certificate
        # Optional RESTful API credentials. The Conversational AI control API
        # authenticates with HTTP Basic auth (Customer ID / Secret from the
        # Agora console). When these are present we use Basic auth; otherwise we
        # fall back to Token007 (app_id + app_certificate), which some projects
        # reject with "401 Invalid token".
        self.customer_id = settings.customer_id
        self.customer_secret = settings.customer_secret
        # Empty by default: wake/open should not automatically become a chat turn.
        self.greeting = settings.greeting
        # Keep the cloud agent alive long enough for natural multi-turn voice.
        self.idle_timeout = settings.idle_timeout
        self.stt_language = settings.stt_language
        self.agent_preset = settings.agent_preset
        self.tts_voice_id = settings.tts_voice_id

        if not self.app_id or not self.app_certificate:
            raise ValueError("AGORA_APP_ID and AGORA_APP_CERTIFICATE are required")

        client_kwargs = {
            "area": Area.US,
            "app_id": self.app_id,
            "app_certificate": self.app_certificate,
        }
        if self.customer_id and self.customer_secret:
            client_kwargs["customer_id"] = self.customer_id
            client_kwargs["customer_secret"] = self.customer_secret
            logger.info("Agora client using RESTful Basic auth (customer_id/secret)")
        else:
            logger.info("Agora client using Token007 auth (app_id/app_certificate)")

        self.client = client_factory(**client_kwargs)

        # Track active sessions by agent_id
        self._sessions: Dict[str, Any] = {}

    def _request_options(self, channel_name: str, agent_uid: int) -> Optional[Dict[str, Any]]:
        if getattr(self.client, "auth_mode", None) != "app-credentials":
            return None
        token = generate_convo_ai_token(
            app_id=self.app_id,
            app_certificate=self.app_certificate,
            channel_name=channel_name,
            uid=agent_uid,
        )
        return {"additional_headers": {"Authorization": f"agora token={token}"}}

    def _build_start_request(
        self,
        *,
        channel_name: str,
        agent_uid: int,
        user_uid: int,
        output_audio_codec: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        token = generate_convo_ai_token(
            app_id=self.app_id,
            app_certificate=self.app_certificate,
            channel_name=channel_name,
            uid=agent_uid,
            token_expire=3600,
        )

        # Transcripts: do NOT set data_channel="rtm" (that routes them to a separate RTM
        # channel our server doesn't join). The API only accepts "rtm"; omitting the key
        # lets transcripts default to the RTC data stream, which our server's
        # IRTCLocalUserObserver.on_stream_message already receives (M4 passphrase matcher).
        parameters: Dict[str, Any] = {"enable_error_message": True}
        if isinstance(output_audio_codec, str) and output_audio_codec.strip():
            parameters["output_audio_codec"] = output_audio_codec.strip()

        properties: Dict[str, Any] = {
            "channel": channel_name,
            "token": token,
            "agent_rtc_uid": str(agent_uid),
            "remote_rtc_uids": [str(user_uid)],
            "idle_timeout": self.idle_timeout,
            "enable_string_uid": False,
            "advanced_features": {"enable_rtm": True, "enable_tools": True},
            "parameters": parameters,
            "turn_detection": {
                "language": self.stt_language,
                "config": {
                    "speech_threshold": 0.6,
                    "start_of_speech": {
                        "mode": "vad",
                        "vad_config": {
                            # Require sustained real speech (1.2s) to interrupt the agent.
                            # 160ms let any brief mic noise/echo truncate the agent mid-sentence.
                            "interrupt_duration_ms": 1200,
                            "prefix_padding_ms": 300,
                        },
                    },
                    "end_of_speech": {
                        "mode": "vad",
                        "vad_config": {
                            "silence_duration_ms": 480,
                        },
                    },
                },
            },
            "asr": {
                "vendor": "deepgram",
                "params": {"language": self.stt_language},
            },
            "llm": {
                "style": "openai",
                "input_modalities": ["text"],
                "system_messages": [{"role": "system", "content": ADA_PROMPT}],
                "greeting_message": self.greeting,
                "failure_message": "稍等我一下下～",
                "max_history": 15,
                "params": {
                    "max_tokens": 1024,
                    "temperature": 0.7,
                    "top_p": 0.95,
                },
            },
            "tts": {
                "vendor": "minimax",
                "params": {
                    # The preset supplies the managed MiniMax model/key. Keep the
                    # requested voice explicit so we can tune it without changing
                    # the whole pipeline.
                    "voice_setting": {"voice_id": self.tts_voice_id},
                },
            },
        }
        return properties, self.agent_preset

    async def start(
        self,
        channel_name: str,
        agent_uid: int,
        user_uid: int,
        output_audio_codec: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start agent with the same default vendor chain as the Next.js quickstart."""
        if not channel_name or not str(channel_name).strip():
            raise ValueError("channel_name is required and cannot be empty")
        if agent_uid <= 0:
            raise ValueError("agent_uid is required and cannot be empty")
        if user_uid <= 0:
            raise ValueError("user_uid is required and cannot be empty")

        name = f"agent_{channel_name}_{agent_uid}_{int(time.time())}"

        # Optional BYOK example: replace the STT block above and set DEEPGRAM_API_KEY.
        # stt = DeepgramSTT(api_key=os.getenv("DEEPGRAM_API_KEY"), model="nova-3", language="en")

        # Optional BYOK example: replace the LLM block above and set OPENAI_API_KEY.
        # llm = OpenAI(
        #     api_key=os.getenv("OPENAI_API_KEY"),
        #     model="gpt-4o-mini",
        #     greeting_message="Hello! I am your AI assistant. How can I help you?",
        #     failure_message="I'm sorry, I'm having trouble processing your request.",
        #     max_history=15,
        #     max_tokens=1024,
        #     temperature=0.7,
        #     top_p=0.95,
        # )

        # Optional BYOK example: replace the TTS block above and set ELEVENLABS_API_KEY.
        # from agora_agent.agentkit.vendors import ElevenLabsTTS
        # tts = ElevenLabsTTS(
        #     key=os.getenv("ELEVENLABS_API_KEY"),
        #     model_id="eleven_flash_v2_5",
        #     voice_id=os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB"),
        # )

        properties, preset = self._build_start_request(
            channel_name=channel_name,
            agent_uid=agent_uid,
            user_uid=user_uid,
            output_audio_codec=output_audio_codec,
        )

        logger.info(
            "Starting Agora agent channel=%s agent_uid=%s user_uid=%s greeting=%r idle_timeout=%s stt_language=%s preset=%s tts_voice_id=%s output_audio_codec=%s",
            channel_name,
            agent_uid,
            user_uid,
            self.greeting,
            self.idle_timeout,
            self.stt_language,
            preset,
            self.tts_voice_id,
            output_audio_codec,
        )

        try:
            response = await self.client.agents.start(
                self.app_id,
                name=name,
                properties=properties,
                preset=preset,
                request_options=self._request_options(channel_name, agent_uid),
            )
            agent_id = response.agent_id
        except Exception:
            logger.exception(
                "Failed to start Agora agent channel=%s agent_uid=%s user_uid=%s",
                channel_name,
                agent_uid,
                user_uid,
            )
            raise

        self._sessions[agent_id] = None

        logger.info(
            "Started Agora agent agent_id=%s channel=%s agent_uid=%s user_uid=%s",
            agent_id,
            channel_name,
            agent_uid,
            user_uid,
        )
        
        return {
            "agent_id": agent_id,
            "channel_name": channel_name,
            "status": "started",
        }

    async def stop(self, agent_id: str) -> None:
        """Stop a running agent. Falls back to the stateless client path."""
        if not agent_id or not str(agent_id).strip():
            raise ValueError("agent_id is required and cannot be empty")

        session = self._sessions.pop(agent_id, None)
        if session:
            try:
                await session.stop()
                logger.info("Stopped Agora agent from active session agent_id=%s", agent_id)
                return
            except Exception:
                # Fall back to the stateless SDK path if the in-memory session is stale.
                logger.warning(
                    "Failed to stop Agora agent from active session; falling back to client.stop_agent agent_id=%s",
                    agent_id,
                    exc_info=True,
                )

        logger.info("Stopping Agora agent through client.stop_agent agent_id=%s", agent_id)
        await self.client.stop_agent(agent_id)
