from __future__ import annotations

import queue
import threading

from openclaw_voice_control import VoiceControlService, VoiceEvent
from openclaw_voice_control.config import load_config


class QueuePresenter:
    """Minimal thread-safe bridge for an external desktop application."""

    def __init__(self) -> None:
        self.events: queue.Queue[VoiceEvent] = queue.Queue()

    def emit(self, event: VoiceEvent) -> None:
        self.events.put(event)


def main() -> None:
    presenter = QueuePresenter()
    service = VoiceControlService(load_config(), presenter=presenter)

    service_thread = threading.Thread(target=service.run, name="voice-core", daemon=True)
    service_thread.start()

    try:
        while service_thread.is_alive():
            event = presenter.events.get()
            print(event.kind.value, event.text or event.user_text, dict(event.metadata))
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
        service_thread.join(timeout=5)


if __name__ == "__main__":
    main()
