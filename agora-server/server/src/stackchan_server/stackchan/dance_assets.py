"""Single on-disk dance.json state for StackChan push playback."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .dance_channel import validate_sequence

STATIC_DIR = Path(__file__).resolve().parents[1] / "static" / "dance"
CURRENT_DANCE_PATH = Path(os.getenv("XZ_DANCE_JSON", str(STATIC_DIR / "dance.json")))


def load_dance_json(path: Path = CURRENT_DANCE_PATH) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_sequence(data)


def save_dance_json(sequence: Any, path: Path = CURRENT_DANCE_PATH) -> list[dict]:
    data = validate_sequence(sequence)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
