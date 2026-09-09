from __future__ import annotations

import threading
from pathlib import Path

from openclaw_voice_control import Presenter, VoiceControlService, VoiceEvent, VoiceEventKind
from openclaw_voice_control.config import load_config


class RecordingPresenter(Presenter):
    def __init__(self) -> None:
        self.events: list[VoiceEvent] = []

    def emit(self, event: VoiceEvent) -> None:
        self.events.append(event)


class FakeASR:
    def transcribe(self, path: str) -> str:
        assert Path(path).is_file()
        return "识别文本"


class FakeClient:
    def ask(self, text: str) -> str:
        return f"reply:{text}"

    def ask_streaming(self, text: str, on_sentence) -> str:
        reply = f"reply:{text}"
        on_sentence(reply)
        return reply

    def close(self) -> None:
        return None


class FakeItem:
    def __init__(self) -> None:
        self.completion = threading.Event()
        self.completion.set()
        self.error = None


class FakeSpeech:
    def __init__(self, service: VoiceControlService) -> None:
        self.service = service
        self.stopped = False

    def clear_stop_request(self) -> None:
        self.service.runtime.clear_stop_speech()

    def enqueue(self, text: str, *, metadata=None, clean_markdown=True, on_complete=None):
        del clean_markdown
        event_metadata = dict(metadata or {})
        self.service._emit(VoiceEvent(VoiceEventKind.SPEAKING, text=text, metadata=event_metadata))
        if on_complete is not None:
            on_complete(True, None)
        return FakeItem()

    def wait_done(self, timeout=None) -> bool:
        del timeout
        return True

    def stop(self, timeout=None, emit_idle=True) -> bool:
        del timeout, emit_idle
        self.stopped = True
        self.service.runtime.request_stop_speech()
        return True

    def close(self) -> None:
        return None


def test_be_t12_external_presenter_and_public_apis_smoke(tmp_path) -> None:
    presenter = RecordingPresenter()
    config_path = Path(__file__).resolve().parents[1] / "config" / "default.yaml"
    service = VoiceControlService(load_config(config_path), presenter=presenter)
    service.asr = FakeASR()
    service.client = FakeClient()
    service.speech = FakeSpeech(service)

    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"fake-audio")
    assert service.transcribe_file(audio_path) == "识别文本"

    assert service.ask_text("你好", speak=False, metadata={"source": "external"}) == "reply:你好"
    assert [event.kind for event in presenter.events[-3:]] == [
        VoiceEventKind.THINKING,
        VoiceEventKind.REPLY,
        VoiceEventKind.IDLE,
    ]

    presenter.events.clear()
    service.speak_message("主动消息", metadata={"source": "external", "message_id": "m1"}, wait=True)
    assert [event.kind for event in presenter.events] == [VoiceEventKind.SPEAKING, VoiceEventKind.IDLE]
    assert presenter.events[0].metadata["message_id"] == "m1"

    service.stop_speaking()
    assert service.speech.stopped is True
    assert presenter.events[-1].kind == VoiceEventKind.IDLE
    assert presenter.events[-1].metadata["source"] == "stop_speaking"
