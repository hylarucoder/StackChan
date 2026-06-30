"""SRT lyrics parsing and on-disk lyric state for StackChan stage playback.

The choreographer UI overlays time-synced lyrics on the stage; this module turns
an ``.srt`` file into a compact JSON cue list (``start``/``end`` in seconds).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TypedDict

from .dance_assets import STATIC_DIR

CURRENT_LYRICS_PATH = Path(os.getenv("XZ_LYRICS_SRT", str(STATIC_DIR / "lyrics.srt")))

_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


class LyricCue(TypedDict):
    index: int
    start: float
    end: float
    text: str


def _stamp_to_seconds(hh: str, mm: str, ss: str, ms: str) -> float:
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(text: str) -> list[LyricCue]:
    """Parse SRT text into ordered cues; tolerant of BOM, CRLF, and trailing spaces."""
    blocks = re.split(r"\r?\n\r?\n+", text.lstrip("﻿").strip())
    cues: list[LyricCue] = []
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        time_idx = 0 if _TIME_RE.search(lines[0]) else 1
        if time_idx >= len(lines):
            continue
        match = _TIME_RE.search(lines[time_idx])
        if not match:
            continue
        body = " ".join(line.strip() for line in lines[time_idx + 1 :]).strip()
        if not body:
            continue
        start = _stamp_to_seconds(*match.group(1, 2, 3, 4))
        end = _stamp_to_seconds(*match.group(5, 6, 7, 8))
        cues.append(
            {"index": len(cues), "start": start, "end": max(end, start), "text": body}
        )
    return cues


def load_lyrics(path: Path = CURRENT_LYRICS_PATH) -> list[LyricCue]:
    """Load and parse the lyrics SRT; returns an empty list when none is present."""
    if not path.exists():
        return []
    return parse_srt(path.read_text(encoding="utf-8"))
