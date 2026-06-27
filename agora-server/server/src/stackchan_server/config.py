from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv


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
