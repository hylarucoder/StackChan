"""Dedicated dance side-channel: deliver a choreography to a device, decoupled from voice.

The device opens a SECOND WebSocket (separate from the XiaoZhi voice `/ws`) just for
dance, at ``/dance/ws?deviceId=<id>``. The server registers it by device id and pushes
``DanceSequence`` (0x14) binary frames down it. The device feeds each frame to
``parse_sequence_from_json`` -> ``DanceModifier`` (the existing playback engine).

Wire frame (matches firmware hal_ws_avatar.cpp and the old Go relay):

    [ msgType : 1 byte ][ length : 4 bytes big-endian ][ payload : length bytes ]

For a dance, msgType = 0x14 and payload = the dance.json bytes (a keyframe array).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

# DataType.DanceSequence in firmware hal_ws_avatar.cpp.
DANCE = 0x14


def normalize_device_id(device_id: str) -> str:
    """Make MAC-like device ids stable across firmware, UI, and curl input."""
    return (device_id or "unknown").strip().lower()


def frame_dance(payload: bytes) -> bytes:
    """Wrap a dance payload in a [0x14][len big-endian][payload] binary frame."""
    return bytes([DANCE]) + struct.pack(">I", len(payload)) + payload


def parse_frame(frame: bytes) -> tuple[int, bytes]:
    """Inverse of frame_dance / frame_message; raises on a malformed frame."""
    if len(frame) < 5:
        raise ValueError("frame too short for 5-byte header")
    msg_type = frame[0]
    length = struct.unpack(">I", frame[1:5])[0]
    payload = frame[5:]
    if len(payload) != length:
        raise ValueError(f"length mismatch: header={length} actual={len(payload)}")
    return msg_type, payload


def validate_sequence(sequence: Any) -> list[dict]:
    """Ensure the object is a list of keyframes the firmware can parse."""
    if not isinstance(sequence, list):
        raise ValueError("dance sequence must be a JSON array of keyframes")
    for i, kf in enumerate(sequence):
        if not isinstance(kf, dict):
            raise ValueError(f"keyframe {i} is not an object")
        for key in ("yawServo", "pitchServo", "durationMs"):
            if key not in kf:
                raise ValueError(f"keyframe {i} missing '{key}'")
    return sequence


def sequence_to_payload(sequence: list[dict]) -> bytes:
    """Compact-serialize a validated keyframe list to the 0x14 payload bytes."""
    return json.dumps(sequence, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def load_dance_payload(path: Path) -> bytes:
    """Load a dance.json file, validate, and return the compact payload bytes."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return sequence_to_payload(validate_sequence(data))


class DanceRegistry:
    """Tracks live dance-channel connections, keyed by device id.

    Holds at most one connection per device; a reconnect replaces the old one.
    Storage is a plain dict — the FastAPI app drives it from a single event loop.
    """

    def __init__(self) -> None:
        self._conns: dict[str, Any] = {}

    def register(self, device_id: str, ws: Any) -> None:
        self._conns[normalize_device_id(device_id)] = ws

    def unregister(self, device_id: str, ws: Any) -> None:
        # Only drop it if the stored connection is still this one (avoid races on reconnect).
        key = normalize_device_id(device_id)
        if self._conns.get(key) is ws:
            self._conns.pop(key, None)

    def get(self, device_id: str) -> Any | None:
        return self._conns.get(normalize_device_id(device_id))

    def device_ids(self) -> list[str]:
        return list(self._conns.keys())

    def __len__(self) -> int:
        return len(self._conns)

    async def push_payload(self, device_id: str, payload: bytes) -> int:
        """Frame `payload` as 0x14 and send it to the device. Returns frame size.

        Raises KeyError if the device has no dance connection.
        """
        ws = self._conns.get(normalize_device_id(device_id))
        if ws is None:
            raise KeyError(device_id)
        frame = frame_dance(payload)
        await ws.send_bytes(frame)
        return len(frame)
