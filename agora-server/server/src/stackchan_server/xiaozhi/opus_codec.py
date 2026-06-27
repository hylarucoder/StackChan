# -*- coding: utf-8 -*-
"""
Opus encode/decode for the device <-> server leg.

The device sends/receives Opus (mono, 60ms frames). Agora wants PCM16. This is a single
decode (uplink) / encode (downlink) -- not a transcode.

Includes a loader shim so ctypes finds libopus on macOS (Homebrew) and Linux without
needing DYLD_FALLBACK_LIBRARY_PATH in the environment.
"""
from __future__ import annotations

import ctypes.util
import os

_orig_find = ctypes.util.find_library


def _find_library(name):
    found = _orig_find(name)
    if found:
        return found
    if name == "opus":
        for p in (
            "/opt/homebrew/lib/libopus.dylib",   # macOS arm64 (Homebrew)
            "/usr/local/lib/libopus.dylib",      # macOS x86_64 (Homebrew)
            "/usr/lib/x86_64-linux-gnu/libopus.so.0",  # Debian/Ubuntu
            "/usr/lib/libopus.so.0",
            "libopus.so.0",
        ):
            if os.path.exists(p) or not p.startswith("/"):
                return p
    return found


ctypes.util.find_library = _find_library

import opuslib  # noqa: E402

SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_MS = 60
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000   # 960
FRAME_BYTES = FRAME_SAMPLES * 2                   # 1920 (PCM16 mono)


class OpusCodec:
    """One per direction (encoder + decoder are cheap; keep one of each per session)."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS,
                 frame_ms: int = FRAME_MS):
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_samples = sample_rate * frame_ms // 1000
        self._enc = opuslib.Encoder(sample_rate, channels, opuslib.APPLICATION_VOIP)
        self._dec = opuslib.Decoder(sample_rate, channels)

    def decode(self, opus_packet: bytes) -> bytes:
        """Opus packet -> PCM16 bytes (one frame)."""
        return self._dec.decode(opus_packet, self.frame_samples)

    def encode(self, pcm16: bytes) -> bytes:
        """One frame of PCM16 (frame_samples*2 bytes) -> Opus packet."""
        return self._enc.encode(pcm16, self.frame_samples)


if __name__ == "__main__":
    import math
    import struct
    c = OpusCodec()
    pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / c.sample_rate)))
                   for n in range(c.frame_samples))
    enc = c.encode(pcm)
    dec = c.decode(enc)
    print(f"encode {len(pcm)}B -> {len(enc)}B opus -> decode {len(dec)}B")
    print("self-test:", "OK" if len(dec) == c.frame_samples * 2 else "FAIL")
