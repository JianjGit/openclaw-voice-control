from __future__ import annotations

import queue
import threading

from openclaw_voice_control import VoiceControlService, VoiceEvent
from openclaw_voice_control.config import load_config


class QueuePresenter:
    def __init__(self) -> None:
        self.events: queue.Queue[VoiceEvent] = queue.Queue()

    def emit(self, event: VoiceEvent) -> None:
        self.events.put(event)


def wait_until_ready(presenter: QueuePresenter, timeout: float = 120.0) -> None:
    while True:
        event = presenter.events.get(timeout=timeout)
        print(event.kind.value, dict(event.metadata), event.text)
        if event.kind.value == "idle" and event.metadata.get("source") == "startup":
            return


def set_mode(service: VoiceControlService, mode: str) -> dict[str, str | None]:
    applied = service.set_input_mode(mode)
    return {
        "status": "applied" if applied else "pending",
        "mode": service.get_input_mode(),
        "pending_mode": service.get_pending_input_mode(),
    }


def main() -> None:
    presenter = QueuePresenter()
    service = VoiceControlService(
        load_config("config/default.yaml", ".env"),
        presenter=presenter,
    )

    voice_thread = threading.Thread(
        target=service.run,
        name="voice-core",
        daemon=True,
    )
    voice_thread.start()

    try:
        # A real bridge should not accept microphone commands until the startup
        # idle event confirms that ASR/STT/input-mode initialization is ready.
        wait_until_ready(presenter)

        # In a real bridge these would be commands from the desktop pet.
        print(set_mode(service, "push_to_talk"))

        # listen_once() is blocking, so a GUI/bridge should submit it to a worker.
        success = service.listen_once(
            speak=True,
            metadata={"source": "desktop_pet", "request_id": "voice-001"},
        )
        print(f"listen_once success={success}")

        print(set_mode(service, "wakeword"))

        # Presenter events can be forwarded to the GUI. A deferred mode switch
        # is confirmed by an idle event with metadata.source == "input_mode".
        while not presenter.events.empty():
            event = presenter.events.get_nowait()
            print(event.kind.value, dict(event.metadata), event.text)
    finally:
        service.close()
        voice_thread.join(timeout=5)


if __name__ == "__main__":
    main()
