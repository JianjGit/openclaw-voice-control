from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class VoiceEventKind(StrEnum):
    """Stable event kinds exposed to SDK consumers."""

    LISTENING = "listening"
    RECOGNIZED = "recognized"
    THINKING = "thinking"
    REPLY = "reply"
    SPEAKING = "speaking"
    IDLE = "idle"
    ERROR = "error"


@dataclass(slots=True)
class VoiceEvent:
    """UI-framework-neutral event emitted by the voice core."""

    kind: VoiceEventKind
    text: str = ""
    user_text: str = ""
    auto_hide_ms: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
