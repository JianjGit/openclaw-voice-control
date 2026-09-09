from __future__ import annotations

import logging
from typing import Protocol

from .events import VoiceEvent


class Presenter(Protocol):
    """Consumes voice-core events.

    ``emit`` may be called from the service thread or a speech worker thread.
    GUI consumers are responsible for marshaling events onto their UI thread.
    """

    def emit(self, event: VoiceEvent) -> None:
        ...


class NullPresenter:
    """Default presenter for headless/embedded operation."""

    def emit(self, event: VoiceEvent) -> None:
        return None


class ConsolePresenter:
    """Development presenter that logs each event without UI dependencies."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("openclaw.voice_control.presenter")

    def emit(self, event: VoiceEvent) -> None:
        self.logger.info(
            "voice_event kind=%s text=%r user_text=%r auto_hide_ms=%d metadata=%r",
            event.kind.value,
            event.text,
            event.user_text,
            event.auto_hide_ms,
            dict(event.metadata),
        )
