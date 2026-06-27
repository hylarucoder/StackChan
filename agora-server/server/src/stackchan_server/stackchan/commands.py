from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import itertools
import re
import threading
import time
from typing import Deque, Dict, Optional
from uuid import uuid4


@dataclass(frozen=True)
class DeviceCommand:
    command_id: str
    sequence: int
    action: str
    source: str
    created_at: float
    text: Optional[str] = None
    parameters: Dict[str, str] = field(default_factory=dict)


class CommandRouter:
    _WAKE_WORDS = {
        "小智",
        "你好小智",
        "你好小只",
        "小志",
        "你好小志",
        "stackchan",
        "stack酱",
        "hey stack chan",
        "hi stack chan",
    }
    _WAKE_NORMALIZE_RE = re.compile(r"[\s,，.。!！?？:：;；~～'\"“”‘’、-]+")

    def __init__(self) -> None:
        self._queues: Dict[str, Deque[DeviceCommand]] = defaultdict(deque)
        self._sequence = itertools.count(1)
        self._lock = threading.Lock()

    def resolve_text(self, text: str) -> Optional[DeviceCommand]:
        normalized = (text or "").strip().lower()
        if not normalized:
            return None
        if self.is_wake_word_only(text):
            return None

        if (
            any(keyword in normalized for keyword in ("跳舞", "dance", "摇一摇"))
            or ("跳" in normalized and "舞" in normalized)
        ):
            return self._new_command(action="dance", source="voice", text=text)
        if any(keyword in normalized for keyword in ("唱", "sing", "song", "歌曲")):
            return self._new_command(action="sing", source="voice", text=text)
        if any(keyword in normalized for keyword in ("停止", "停下", "stop", "暂停")):
            return self._new_command(action="stop", source="voice", text=text)
        return None

    @classmethod
    def is_wake_word_only(cls, text: str) -> bool:
        normalized = cls._normalize_wake_word_text(text)
        if not normalized:
            return False
        return normalized in {cls._normalize_wake_word_text(word) for word in cls._WAKE_WORDS}

    def enqueue(
        self,
        device_id: str,
        action: str,
        source: str,
        text: Optional[str] = None,
        parameters: Optional[Dict[str, str]] = None,
    ) -> DeviceCommand:
        command = self._new_command(
            action=action,
            source=source,
            text=text,
            parameters=parameters,
        )
        with self._lock:
            self._queues[self._normalize_device_id(device_id)].append(command)
        return command

    def enqueue_resolved_text(self, device_id: str, text: str) -> Optional[DeviceCommand]:
        command = self.resolve_text(text)
        if command is None:
            return None
        with self._lock:
            self._queues[self._normalize_device_id(device_id)].append(command)
        return command

    def poll_next(self, device_id: str) -> Optional[DeviceCommand]:
        with self._lock:
            queue = self._queues[self._normalize_device_id(device_id)]
            if not queue:
                return None
            return queue.popleft()

    def _new_command(
        self,
        action: str,
        source: str,
        text: Optional[str] = None,
        parameters: Optional[Dict[str, str]] = None,
    ) -> DeviceCommand:
        return DeviceCommand(
            command_id=str(uuid4()),
            sequence=next(self._sequence),
            action=action,
            source=source,
            created_at=time.time(),
            text=text,
            parameters=dict(parameters or {}),
        )

    @staticmethod
    def _normalize_device_id(device_id: str) -> str:
        normalized = (device_id or "").strip()
        if not normalized:
            raise ValueError("device_id is required")
        return normalized

    @classmethod
    def _normalize_wake_word_text(cls, text: str) -> str:
        return cls._WAKE_NORMALIZE_RE.sub("", (text or "").strip().lower())
