# -*- coding: utf-8 -*-
"""
XiaoZhi WebSocket protocol helpers (server side).

Reference: docs/research/xiaozhi-ws-agora-relay-server.md §4 and the firmware source
stackchan-firmware/xiaozhi-esp32/main/protocols/websocket_protocol.cc.

This module only deals with message *shapes* + binary audio framing. It has no I/O.
"""
from __future__ import annotations

import struct
from typing import Any, Dict, Optional

# --- Audio defaults (server -> device hello) -------------------------------
# Device uplink is fixed: Opus / mono / 16k / 60ms. Downlink sample_rate is whatever
# we advertise here; the device adopts it. Keep 16k to avoid resampling against Agora STT/TTS.
DEFAULT_DOWNLINK_SAMPLE_RATE = 16000
FRAME_DURATION_MS = 60
UPLINK_SAMPLE_RATE = 16000
CHANNELS = 1


# --- Text control messages -------------------------------------------------
def server_hello(session_id: str,
                 sample_rate: int = DEFAULT_DOWNLINK_SAMPLE_RATE,
                 frame_duration: int = FRAME_DURATION_MS) -> Dict[str, Any]:
    """Reply to the device's client hello. `transport` MUST be 'websocket'."""
    return {
        "type": "hello",
        "transport": "websocket",
        "session_id": session_id,
        "audio_params": {
            "sample_rate": sample_rate,
            "frame_duration": frame_duration,
        },
    }


def tts_start(session_id: str) -> Dict[str, Any]:
    # MUST be sent before streaming downlink audio, else the device drops frames
    # received outside the Speaking state (application.cc:498-502).
    return {"session_id": session_id, "type": "tts", "state": "start"}


def tts_stop(session_id: str) -> Dict[str, Any]:
    return {"session_id": session_id, "type": "tts", "state": "stop"}


def tts_sentence_start(session_id: str, text: str) -> Dict[str, Any]:
    return {"session_id": session_id, "type": "tts", "state": "sentence_start", "text": text}


def stt(session_id: str, text: str) -> Dict[str, Any]:
    return {"session_id": session_id, "type": "stt", "text": text}


def llm_emotion(session_id: str, emotion: str) -> Dict[str, Any]:
    return {"session_id": session_id, "type": "llm", "emotion": emotion}


def mcp(session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"session_id": session_id, "type": "mcp", "payload": payload}


# --- Binary audio framing --------------------------------------------------
# Protocol version 1: the binary WS message body IS the raw Opus packet (no header).
# v2/v3 prepend a header with timestamp (for server-side AEC); not needed for the POC.

def encode_audio_v1(opus_packet: bytes) -> bytes:
    return opus_packet


def decode_audio_v1(data: bytes) -> bytes:
    return data


# v2/v3 kept for completeness / future server-side AEC.
def encode_audio_v2(opus_packet: bytes, timestamp_ms: int = 0) -> bytes:
    # struct BinaryProtocol2: version(u16) type(u16) reserved(u32) timestamp(u32) payload_size(u32)
    header = struct.pack(">HHIII", 2, 0, 0, timestamp_ms & 0xFFFFFFFF, len(opus_packet))
    return header + opus_packet


def decode_audio_v2(data: bytes) -> bytes:
    if len(data) < 16:
        return b""
    _ver, _type, _res, _ts, size = struct.unpack(">HHIII", data[:16])
    return data[16:16 + size]
