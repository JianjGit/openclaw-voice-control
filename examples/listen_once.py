from __future__ import annotations

from openclaw_voice_control import ConsolePresenter, VoiceControlService
from openclaw_voice_control.config import load_config


def main() -> None:
    service = VoiceControlService(
        load_config("config/default.yaml", ".env"),
        presenter=ConsolePresenter(),
    )

    try:
        success = service.listen_once(
            speak=True,
            metadata={"source": "example_push_to_talk"},
        )
        print(f"listen_once success={success}")
    finally:
        service.close()


if __name__ == "__main__":
    main()
