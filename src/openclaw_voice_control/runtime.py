from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass(slots=True)
class RuntimeControl:
    """Thread-safe, UI-independent runtime signals for the voice core."""

    stop_speech_event: threading.Event = field(default_factory=threading.Event)
    shutdown_event: threading.Event = field(default_factory=threading.Event)

    def request_stop_speech(self) -> None:
        self.stop_speech_event.set()

    def clear_stop_speech(self) -> None:
        self.stop_speech_event.clear()

    def is_stop_speech_requested(self) -> bool:
        return self.stop_speech_event.is_set()

    def request_shutdown(self) -> None:
        self.shutdown_event.set()

    def is_shutdown_requested(self) -> bool:
        return self.shutdown_event.is_set()
