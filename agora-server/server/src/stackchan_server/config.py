from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv

logger = logging.getLogger("uvicorn.error")

# How the robot's dance gets triggered. XZ_DANCE_TRIGGER is a comma-separated set of
# modes (both may be on at once):
#   - "keyword": the server matches dance phrases in the user transcript (local-friendly,
#                needs no public endpoint).
#   - "mcp":     the cloud ConvoAI agent decides to call the dance tool (needs
#                XZ_DANCE_MCP_URL reachable by Agora's cloud).
DEFAULT_DANCE_TRIGGER = "keyword"
_VALID_DANCE_MODES = ("keyword", "mcp")


def dance_trigger_modes() -> set[str]:
    """Parse XZ_DANCE_TRIGGER into the set of enabled dance-trigger modes."""
    raw = os.getenv("XZ_DANCE_TRIGGER", DEFAULT_DANCE_TRIGGER)
    modes = {m.strip().lower() for m in raw.split(",") if m.strip()}
    unknown = modes.difference(_VALID_DANCE_MODES)
    if unknown:
        logger.warning(
            "XZ_DANCE_TRIGGER has unknown mode(s) %s; valid modes: %s",
            sorted(unknown),
            list(_VALID_DANCE_MODES),
        )
    return modes.intersection(_VALID_DANCE_MODES)


def dance_keyword_enabled() -> bool:
    return "keyword" in dance_trigger_modes()


def dance_mcp_enabled() -> bool:
    return "mcp" in dance_trigger_modes()


@dataclass(frozen=True)
class EnvironmentLoadResult:
    server_root: Path
    loaded_files: Tuple[Path, ...]


def default_server_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_environment(server_root: Optional[Path] = None) -> EnvironmentLoadResult:
    root = Path(server_root) if server_root is not None else default_server_root()
    loaded = []

    for env_file in (root / ".env", root / ".env.local"):
        if env_file.exists():
            load_dotenv(env_file, override=True)
            loaded.append(env_file)

    return EnvironmentLoadResult(server_root=root, loaded_files=tuple(loaded))
