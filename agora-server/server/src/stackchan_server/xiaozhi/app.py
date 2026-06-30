"""
XiaoZhi <-> Agora bridge server (POC).

Endpoints:
  GET/POST  /xiaozhi/ota/       -> OTA config: tells the device our WebSocket url + token
  WS        /ws                 -> XiaoZhi WebSocket protocol (hello, audio, mcp, ...)
  WS        /dance/ws           -> dedicated dance side-channel (0x14 frames), decoupled from voice
  POST      /trigger/dance      -> make a connected device dance over /dance/ws
  POST      /trigger/dance/push -> push a compiled dance.json to a device over /dance/ws
  GET       /sessions           -> debug: list connected devices
  GET       /xiaozhi/healthz

Run:  uv run stackchan-xiaozhi-bridge
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..config import load_environment
from ..stackchan.dance_assets import (
    CURRENT_DANCE_PATH,
    load_dance_json,
    save_dance_json,
)
from ..stackchan.dance_channel import (
    DanceRegistry,
    sequence_to_payload,
)
from ..stackchan.lyrics import CURRENT_LYRICS_PATH, load_lyrics
from .dance_mcp import build_dance_mcp
from .session import XzSession

load_environment()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn.error")

router = APIRouter()

# device_id -> live session
SESSIONS: dict[str, XzSession] = {}

# device_id -> dedicated dance-channel connection (separate from the voice /ws)
DANCE = DanceRegistry()

# Static device token advertised to the device by OTA. The development fallback
# is intentionally obvious; deployments should set XZ_DEVICE_TOKEN.
DEVICE_TOKEN = os.getenv("XZ_DEVICE_TOKEN", "change-me-dev-token")
# Public ws url advertised by OTA; if unset we derive it from the request host.
PUBLIC_WS_URL = os.getenv("XZ_WS_URL")  # e.g. ws://192.168.1.20:8000/ws
AUTO_VOICE = os.getenv("XZ_AUTO_VOICE", "1") == "1"  # start ConvoAI voice on device connect
IDLE_DISCONNECT_SECONDS = float(os.getenv("XZ_IDLE_DISCONNECT_SECONDS", "30"))


def _ws_url_for(request: Request) -> str:
    if PUBLIC_WS_URL:
        return PUBLIC_WS_URL
    host = request.headers.get("host") or f"{request.url.hostname}:{request.url.port or 8000}"
    return f"ws://{host}/ws"


@router.get("/xiaozhi/healthz")
async def healthz():
    return {"ok": True, "sessions": list(SESSIONS.keys()), "dance_channels": DANCE.device_ids()}


@router.get("/dance/json")
async def dance_json():
    try:
        return load_dance_json(CURRENT_DANCE_PATH)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"bad dance.json: {e}") from e


@router.get("/dance/lyrics")
async def dance_lyrics():
    """Time-synced lyric cues for the current song; empty list when no SRT is present."""
    try:
        return load_lyrics(CURRENT_LYRICS_PATH)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"bad lyrics.srt: {e}") from e


@router.get("/sessions")
async def sessions():
    return {
        "sessions": [
            {
                "device_id": s.device_id,
                "session_id": s.session_id,
                "state": s.state,
                "tools": [t.get("name") for t in s.mcp.tools],
                "dance_channel": DANCE.get(s.device_id) is not None,
            }
            for s in SESSIONS.values()
        ],
        "dance_channels": DANCE.device_ids(),
    }


@router.api_route("/xiaozhi/ota/", methods=["GET", "POST"])
@router.api_route("/xiaozhi/ota", methods=["GET", "POST"])
@router.api_route("/ota", methods=["GET", "POST"])
async def ota(request: Request):
    """Return the device's WebSocket config. The firmware writes the `websocket` block
    into NVS (ota.cc), so the next connection goes to us. No firmware-upgrade offered."""
    device_id = request.headers.get("device-id", "")
    ws_url = _ws_url_for(request)
    logger.info("OTA request device=%s -> ws_url=%s", device_id, ws_url)
    return {
        "websocket": {
            "url": ws_url,
            "token": DEVICE_TOKEN,
            "version": 1,
        },
        # No "firmware" block -> device does not attempt an OTA upgrade.
    }


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    device_id = ws.headers.get("device-id", "") or ws.headers.get("Device-Id", "") or "unknown"
    client_id = ws.headers.get("client-id", "")
    auth = ws.headers.get("authorization", "")
    logger.info("WS connect device=%s client=%s auth=%s",
                device_id, client_id, "yes" if auth else "no")

    session = XzSession(ws, device_id, client_id)
    session.on_dance_request = lambda style: _push_default_dance(device_id, style)
    SESSIONS[device_id] = session
    run_task = asyncio.create_task(session.run())
    idle_task = None
    try:
        if AUTO_VOICE:
            # Auto-start voice the moment the device connects (while it's still in
            # 'listening'), so ConvoAI is in the channel and greets immediately — otherwise
            # the device idles to a void before we attach the agent.
            try:
                await asyncio.wait_for(session.hello_event.wait(), 12)
                from .voice_bridge import VoiceBridge
                vb = VoiceBridge(session, asyncio.get_running_loop())
                session.voice = vb
                logger.info(
                    "AUTO-VOICE: starting for device=%s session=%s",
                    device_id,
                    session.session_id,
                )
                if not await vb.start():
                    session.voice = None
                    logger.error("AUTO-VOICE: start failed")
                elif IDLE_DISCONNECT_SECONDS > 0:
                    idle_task = asyncio.create_task(
                        _idle_disconnect_watchdog(session, IDLE_DISCONNECT_SECONDS)
                    )
            except asyncio.TimeoutError:
                logger.warning("AUTO-VOICE: hello not received in 12s; skipping")
            except Exception:
                logger.exception("AUTO-VOICE: error")
                session.voice = None
        await run_task
    finally:
        if idle_task is not None:
            idle_task.cancel()
        run_task.cancel()
        if session.voice is not None:
            try:
                await session.voice.stop()
            except Exception:
                logger.exception("voice stop on disconnect failed")
            session.voice = None
        if SESSIONS.get(device_id) is session:
            SESSIONS.pop(device_id, None)
        logger.info("WS disconnect device=%s", device_id)


async def _idle_disconnect_watchdog(session: XzSession, timeout_s: float) -> None:
    try:
        while True:
            await asyncio.sleep(1.0)
            voice = session.voice
            if voice is None:
                return
            idle_s = time.monotonic() - voice.last_meaningful_activity_at()
            if idle_s < timeout_s:
                continue
            logger.info(
                "IDLE: closing session device=%s session=%s idle=%.1fs timeout=%.1fs",
                session.device_id,
                session.session_id,
                idle_s,
                timeout_s,
            )
            await session.ws.close(code=1000, reason="idle timeout")
            return
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("IDLE: watchdog error")


@router.websocket("/dance/ws")
async def dance_ws_endpoint(ws: WebSocket, deviceId: str = Query(default="")):
    """Dedicated dance side-channel. The device opens this in addition to the voice
    `/ws`; we register it by device id and push 0x14 DanceSequence frames to it."""
    await ws.accept()
    device_id = deviceId or ws.headers.get("device-id", "") or "unknown"
    DANCE.register(device_id, ws)
    logger.info("DANCE-WS connect device=%s (total=%d)", device_id, len(DANCE))
    try:
        # We only push to the device; drain anything it sends (e.g. acks) until it closes.
        while True:
            await ws.receive()
    except Exception:
        pass
    finally:
        DANCE.unregister(device_id, ws)
        logger.info("DANCE-WS disconnect device=%s (total=%d)", device_id, len(DANCE))


def _current_dance_payload() -> bytes:
    return sequence_to_payload(load_dance_json(CURRENT_DANCE_PATH))


def _save_current_dance_payload(sequence: list) -> bytes:
    save_dance_json(sequence, CURRENT_DANCE_PATH)
    return _current_dance_payload()


class DancePushRequest(BaseModel):
    deviceId: str | None = None
    sequence: list | None = None


@router.post("/trigger/dance/push")
async def trigger_dance_push(req: DancePushRequest):
    """Overwrite the single dance.json, then push it over the dedicated /dance/ws channel."""
    if req.sequence is None:
        raise HTTPException(status_code=400, detail="provide sequence")

    device_id = req.deviceId
    if not device_id:
        ids = DANCE.device_ids()
        if not ids:
            raise HTTPException(status_code=409, detail="no dance channel connected")
        device_id = ids[0]
    elif DANCE.get(device_id) is None:
        raise HTTPException(status_code=404, detail=f"device {device_id} has no dance channel")

    try:
        payload = _save_current_dance_payload(req.sequence)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"bad dance.json: {e}") from e

    try:
        size = await DANCE.push_payload(device_id, payload)
    except KeyError as e:
        raise HTTPException(
            status_code=404, detail=f"device {device_id} has no dance channel") from e

    n_frames = len(json.loads(payload))
    logger.info(
        "DANCE save+push device=%s path=%s frames=%d bytes=%d",
        device_id,
        CURRENT_DANCE_PATH,
        n_frames,
        size,
    )
    return {
        "ok": True,
        "device": device_id,
        "path": str(CURRENT_DANCE_PATH),
        "keyframes": n_frames,
        "frame_bytes": size,
    }


def _pick_dance_device(device: str | None) -> str:
    if device:
        if DANCE.get(device) is None:
            raise HTTPException(status_code=404, detail=f"device {device} has no dance channel")
        return device
    ids = DANCE.device_ids()
    if not ids:
        raise HTTPException(status_code=409, detail="no dance channel connected")
    return ids[0]


async def _push_default_dance(device_id: str, style: str = "happy") -> dict:
    payload = _current_dance_payload()
    size = await DANCE.push_payload(device_id, payload)
    n_frames = len(json.loads(payload))
    logger.info(
        "DANCE current push device=%s style=%s path=%s frames=%d bytes=%d",
        device_id,
        style,
        CURRENT_DANCE_PATH,
        n_frames,
        size,
    )
    return {
        "sent": True,
        "device": device_id,
        "style": style,
        "path": str(CURRENT_DANCE_PATH),
        "keyframes": n_frames,
        "frame_bytes": size,
    }


async def _mcp_push_dance(style: str = "happy") -> dict:
    """Tool-call entry point for the ConvoAI agent's `dance` MCP tool.

    Pushes the current dance to the first device that has a /dance/ws channel.
    Returns a result dict (never raises) so the model always gets a clean answer."""
    ids = DANCE.device_ids()
    if not ids:
        return {"sent": False, "reason": "no dance channel connected"}
    try:
        return await _push_default_dance(ids[0], style)
    except KeyError:
        return {"sent": False, "reason": "dance channel dropped"}


# MCP server the cloud ConvoAI agent calls (registered in agent.py llm.mcp_servers).
DANCE_MCP = build_dance_mcp(_mcp_push_dance)


def _pick_session(device: str | None) -> XzSession:
    if not SESSIONS:
        raise HTTPException(status_code=409, detail="no device connected")
    if device:
        s = SESSIONS.get(device)
        if not s:
            raise HTTPException(status_code=404, detail=f"device {device} not connected")
        return s
    # default: the only / first connected device
    return next(iter(SESSIONS.values()))


@router.post("/trigger/dance")
async def trigger_dance(device: str | None = Query(default=None),
                        style: str = Query(default="happy")):
    """Make a connected device dance over its dedicated /dance/ws channel."""
    device_id = _pick_dance_device(device)
    try:
        result = await _push_default_dance(device_id, style)
        return {"ok": True, "device": device_id, "result": result}
    except KeyError as e:
        raise HTTPException(
            status_code=404, detail=f"device {device_id} has no dance channel") from e
    except Exception as e:
        logger.exception("trigger_dance failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/trigger/mcp")
async def trigger_mcp(device: str | None = Query(default=None),
                      tool: str = Query(...),
                      args: str = Query(default="{}")):
    """Diagnostic: call an arbitrary MCP tool on a connected device and return its result.
    e.g. tool=self.get_device_status, or tool=self.audio_speaker.set_volume args={"volume":90}"""
    import json as _json
    session = _pick_session(device)
    try:
        arguments = _json.loads(args) if args else {}
        result = await session.mcp.call_tool(tool, arguments)
        return {"ok": True, "device": session.device_id, "tool": tool, "result": result}
    except Exception as e:
        logger.exception("trigger_mcp failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/start_voice")
async def start_voice(device: str | None = Query(default=None)):
    """[M3/M4] Start the full voice path for a connected device: media bridge joins an
    Agora channel, ConvoAI agent starts, device audio is piped both ways, and the
    passphrase triggers the dance. Gated (not automatic) to control ConvoAI usage."""
    session = _pick_session(device)
    if session.voice is not None:
        return {"ok": True, "already_running": True, "channel": session.voice.channel}
    from .voice_bridge import VoiceBridge
    vb = VoiceBridge(session, asyncio.get_running_loop())
    session.voice = vb
    try:
        ok = await vb.start()
    except Exception as e:
        session.voice = None
        logger.exception("start_voice failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not ok:
        session.voice = None
        raise HTTPException(status_code=500, detail="voice bridge failed to start")
    return {"ok": True, "channel": vb.channel, "user_uid": vb.user_uid, "agent_uid": vb.agent_uid}


@router.post("/stop_voice")
async def stop_voice(device: str | None = Query(default=None)):
    session = _pick_session(device)
    if session.voice is None:
        return {"ok": True, "not_running": True}
    await session.voice.stop()
    session.voice = None
    return {"ok": True}


def create_router() -> APIRouter:
    return router


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # FastMCP's streamable-http session manager must run for the lifetime of the app.
    # Starlette does not propagate lifespan to mounted sub-apps, so we drive it here.
    async with DANCE_MCP.session_manager.run():
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="StackChan XiaoZhi<->Agora Bridge", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(create_router())
    # Mounted at /dance-mcp; the tool endpoint is /dance-mcp/mcp (FastMCP default path).
    app.mount("/dance-mcp", DANCE_MCP.streamable_http_app())
    return app


app = create_app()


def main() -> None:
    """Run the XiaoZhi bridge app with Uvicorn for local development."""
    import uvicorn

    port = int(os.getenv("XZ_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
