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
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from . import protocol as proto
from .mcp import McpHost

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
        self.voice = None  # VoiceBridge when M3 voice is started for this session
        self.hello_event = asyncio.Event()  # set once the hello handshake completes

    # --- low level send -----------------------------------------------------
    async def send_text(self, obj: dict[str, Any]) -> None:
        await self.ws.send_text(json.dumps(obj, ensure_ascii=False))

    async def send_bytes(self, data: bytes) -> None:
        await self.ws.send_bytes(data)

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
        await self.send_text(proto.tts_start(self.session_id))
        self.state = "speaking"

    async def push_tts_frame(self, opus_frame: bytes) -> None:
        await self.send_bytes(proto.encode_audio_v1(opus_frame))

    async def end_tts(self) -> None:
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
        logger.info("WS text<- %s", text[:220])
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("session %s: bad JSON text frame", self.session_id)
            return
        mtype = obj.get("type")
        if mtype == "hello":
            await self._on_hello(obj)
        elif mtype == "mcp":
            self.mcp.feed(obj.get("payload") or {})
        elif mtype == "listen":
            logger.info("session %s: listen %s", self.session_id, obj.get("state"))
        elif mtype == "abort":
            logger.info("session %s: abort reason=%s", self.session_id, obj.get("reason"))
            await self._on_abort()
        else:
            logger.debug("session %s: unhandled msg type=%s", self.session_id, mtype)

    async def _on_binary(self, data: bytes) -> None:
        self._bin_n = getattr(self, "_bin_n", 0) + 1
        if self._bin_n <= 2 or self._bin_n % 100 == 0:
            logger.info("session %s: device audio frame #%d (%dB) voice=%s",
                        self.session_id, self._bin_n, len(data), self.on_audio_frame is not None)
        if self.on_audio_frame is not None:
            res = self.on_audio_frame(proto.decode_audio_v1(data))
            if asyncio.iscoroutine(res):
                await res
        # M0: no audio handler -> ignore device uplink

    async def _on_hello(self, obj: dict[str, Any]) -> None:
        self.device_audio = obj.get("audio_params", {}) or {}
        await self.send_text(proto.server_hello(self.session_id, self.downlink_sample_rate))
        self.state = "listening"
        logger.info("session %s: hello from device=%s audio=%s",
                    self.session_id, self.device_id, self.device_audio)
        self.hello_event.set()
        # MCP discovery in the background so the receive loop keeps draining replies
        asyncio.create_task(self._discover_mcp())

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
