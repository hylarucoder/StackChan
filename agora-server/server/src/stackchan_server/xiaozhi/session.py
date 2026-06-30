"""
One XiaoZhi device session over a WebSocket.

M0 scope: hello handshake + MCP discovery + server-driven dance.
Audio hooks (on_audio_frame / downlink) are stubbed here and filled by M1+ (loopback)
and M3+ (Agora media bridge).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from . import protocol as proto
from .mcp import McpHost

# Server->device MCP discovery (initialize + tools/list). Some firmware advertises
# mcp:true but doesn't answer; the failed handshake may also disturb the device's
# audio pipeline. Set XZ_MCP_DISCOVERY=0 to skip it while debugging the voice path.
MCP_DISCOVERY_ENABLED = os.getenv("XZ_MCP_DISCOVERY", "1") == "1"

logger = logging.getLogger("uvicorn.error")

class XzSession:
    def __init__(self, ws, device_id: str, client_id: str = ""):
        self.ws = ws
        self.device_id = device_id
        self.client_id = client_id
        self.session_id = uuid.uuid4().hex
        self.state = "idle"
        self.downlink_sample_rate = proto.DEFAULT_DOWNLINK_SAMPLE_RATE
        self.device_audio: dict[str, Any] = {}
        self.mcp = McpHost(self._send_mcp_payload)
        self._closed = False
        # filled by later milestones
        self.on_audio_frame: Callable[[bytes], Any] | None = None
        self.on_dance_request: Callable[[str], Awaitable[Any]] | None = None
        self.before_hello_response: Callable[[], Awaitable[Any]] | None = None
        self.voice = None  # VoiceBridge when M3 voice is started for this session
        self.hello_event = asyncio.Event()  # set once the hello handshake completes
        self.created_at = time.monotonic()
        self.last_text_in_at = 0.0
        self.last_text_out_at = 0.0
        self.last_audio_in_at = 0.0
        self.last_audio_out_at = 0.0
        self.last_listen_state = ""
        self.last_listen_mode = ""
        self._text_in_n = 0
        self._text_out_n = 0
        self._bin_n = 0
        self._bytes_out_n = 0
        self._tts_frame_n = 0

    # --- low level send -----------------------------------------------------
    async def send_text(self, obj: dict[str, Any]) -> None:
        payload = json.dumps(obj, ensure_ascii=False)
        await self.ws.send_text(payload)
        self._text_out_n += 1
        self.last_text_out_at = time.monotonic()
        logger.info(
            "WS->device text #%d session=%s type=%s state=%s bytes=%d",
            self._text_out_n,
            self.session_id,
            obj.get("type", ""),
            obj.get("state", ""),
            len(payload.encode("utf-8")),
        )

    async def send_bytes(self, data: bytes) -> None:
        await self.ws.send_bytes(data)
        self._bytes_out_n += 1
        self.last_audio_out_at = time.monotonic()

    async def _send_mcp_payload(self, payload: dict[str, Any]) -> None:
        await self.send_text(proto.mcp(self.session_id, payload))

    # --- downlink audio (used by M1/M3) ------------------------------------
    async def speak_opus(self, opus_frames: list[bytes]) -> None:
        """Send a burst of Opus frames as a TTS turn (tts/start ... frames ... tts/stop)."""
        await self.send_text(proto.tts_start(self.session_id))
        self.state = "speaking"
        for f in opus_frames:
            await self.send_bytes(proto.encode_audio_v1(f))
        await self.send_text(proto.tts_stop(self.session_id))
        self.state = "listening"

    async def begin_tts(self) -> None:
        logger.info("TTS device-send session=%s text=start", self.session_id)
        self._tts_frame_n = 0
        await self.send_text(proto.tts_start(self.session_id))
        self.state = "speaking"

    async def push_tts_frame(self, opus_frame: bytes) -> None:
        self._tts_frame_n += 1
        if self._tts_frame_n <= 3 or self._tts_frame_n % 25 == 0:
            logger.info(
                "TTS device-send session=%s audio_frame=%d opus_bytes=%d ws_bytes_out=%d",
                self.session_id,
                self._tts_frame_n,
                len(opus_frame),
                self._bytes_out_n,
            )
        await self.send_bytes(proto.encode_audio_v1(opus_frame))

    async def end_tts(self) -> None:
        logger.info(
            "TTS device-send session=%s text=stop frames=%d",
            self.session_id,
            self._tts_frame_n,
        )
        await self.send_text(proto.tts_stop(self.session_id))
        self.state = "listening"

    # --- dance --------------------------------------------------------------
    async def trigger_dance(self, style: str = "happy") -> Any:
        if self.on_dance_request is None:
            raise RuntimeError("dance push channel is not configured")
        logger.info("session %s: pushing dance style=%s", self.session_id, style)
        return await self.on_dance_request(style)

    # --- main loop ----------------------------------------------------------
    async def run(self) -> None:
        from starlette.websockets import WebSocketDisconnect
        try:
            while not self._closed:
                msg = await self.ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("text") is not None:
                    await self._on_text(msg["text"])
                elif msg.get("bytes") is not None:
                    await self._on_binary(msg["bytes"])
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("session %s loop error", self.session_id)
        finally:
            self._closed = True

    async def _on_text(self, text: str) -> None:
        self._text_in_n += 1
        self.last_text_in_at = time.monotonic()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            logger.info(
                "WS<-device text #%d session=%s bad_json bytes=%d preview=%s",
                self._text_in_n,
                self.session_id,
                len(text.encode("utf-8", "ignore")),
                text[:160],
            )
            logger.warning("session %s: bad JSON text frame", self.session_id)
            return
        mtype = obj.get("type")
        logger.info(
            "WS<-device text #%d session=%s type=%s state=%s mode=%s bytes=%d",
            self._text_in_n,
            self.session_id,
            mtype,
            obj.get("state", ""),
            obj.get("mode", ""),
            len(text.encode("utf-8", "ignore")),
        )
        if mtype == "hello":
            await self._on_hello(obj)
        elif mtype == "mcp":
            self.mcp.feed(obj.get("payload") or {})
        elif mtype == "listen":
            self.last_listen_state = str(obj.get("state", ""))
            self.last_listen_mode = str(obj.get("mode", ""))
            logger.info(
                "TURN state session=%s device_listen=%s mode=%s app_state=%s",
                self.session_id,
                self.last_listen_state,
                self.last_listen_mode,
                self.state,
            )
        elif mtype == "abort":
            logger.info("session %s: abort reason=%s", self.session_id, obj.get("reason"))
            await self._on_abort()
        else:
            logger.debug("session %s: unhandled msg type=%s", self.session_id, mtype)

    async def _on_binary(self, data: bytes) -> None:
        self._bin_n += 1
        self.last_audio_in_at = time.monotonic()
        if self._bin_n <= 3 or self._bin_n % 100 == 0:
            logger.info(
                "WS<-device audio session=%s frame=%d opus_bytes=%d voice_bridge=%s state=%s",
                self.session_id,
                self._bin_n,
                len(data),
                self.on_audio_frame is not None,
                self.state,
            )
        if self.on_audio_frame is not None:
            res = self.on_audio_frame(proto.decode_audio_v1(data))
            if asyncio.iscoroutine(res):
                await res
        # M0: no audio handler -> ignore device uplink

    async def _on_hello(self, obj: dict[str, Any]) -> None:
        self.device_audio = obj.get("audio_params", {}) or {}
        if self.before_hello_response is not None:
            await self.before_hello_response()
        await self.send_text(proto.server_hello(self.session_id, self.downlink_sample_rate))
        self.state = "listening"
        logger.info("TURN state session=%s hello device=%s audio=%s",
                    self.session_id, self.device_id, self.device_audio)
        self.hello_event.set()
        # MCP discovery in the background so the receive loop keeps draining replies
        if MCP_DISCOVERY_ENABLED:
            asyncio.create_task(self._discover_mcp())
        else:
            logger.info("session %s: MCP discovery disabled (XZ_MCP_DISCOVERY=0)", self.session_id)

    async def _discover_mcp(self) -> None:
        try:
            await self.mcp.initialize()
            await self.mcp.list_tools()
            logger.info("session %s: MCP tools discovered=%d", self.session_id, len(self.mcp.tools))
        except Exception:
            logger.exception("session %s: MCP discovery failed", self.session_id)

    async def _on_abort(self) -> None:
        # M3+: stop ConvoAI speech + flush downlink. M0: nothing.
        self.state = "listening"

    def debug_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()

        def age(ts: float) -> float | None:
            return round(now - ts, 3) if ts else None

        return {
            "age_seconds": round(now - self.created_at, 3),
            "text_in": self._text_in_n,
            "text_out": self._text_out_n,
            "audio_in_frames": self._bin_n,
            "audio_out_frames": self._bytes_out_n,
            "tts_out_frames": self._tts_frame_n,
            "last_listen_state": self.last_listen_state,
            "last_listen_mode": self.last_listen_mode,
            "last_text_in_age": age(self.last_text_in_at),
            "last_text_out_age": age(self.last_text_out_at),
            "last_audio_in_age": age(self.last_audio_in_at),
            "last_audio_out_age": age(self.last_audio_out_at),
        }
