from __future__ import annotations

import threading
import time

import pytest

from openclaw_voice_control.events import VoiceEventKind
from openclaw_voice_control.runtime import RuntimeControl
from openclaw_voice_control.service import VoiceControlService
from openclaw_voice_control.speech import SpeechController


class FakeBackend:
    def __init__(self, *, block_until_stop: bool = False, fail: bool = False) -> None:
        self.block_until_stop = block_until_stop
        self.fail = fail
        self.started = threading.Event()
        self.spoken: list[str] = []

    def open(self) -> None:
        return None

    def speak(self, text: str, should_stop) -> bool:
        self.spoken.append(text)
        self.started.set()
        if self.fail:
            raise RuntimeError("speech failed")
        if self.block_until_stop:
            while not should_stop():
                time.sleep(0.005)
            return False
        return not should_stop()

    def close(self) -> None:
        return None


def _service(backend: FakeBackend):
    service = VoiceControlService.__new__(VoiceControlService)
    service.events = []
    service._emit = service.events.append
    service.runtime = RuntimeControl()
    service.speech = SpeechController(backend, service.runtime, service._emit)
    return service


def test_be_t06_speak_message_emits_speaking_idle_and_metadata() -> None:
    backend = FakeBackend()
    service = _service(backend)

    service.speak_message(
        "hello",
        metadata={"source": "external", "message_id": "m1"},
        wait=True,
    )

    # clean_text_for_tts() in dev intentionally terminates non-empty speech with `。`.
    assert backend.spoken == ["hello。"]
    assert [event.kind for event in service.events] == [
        VoiceEventKind.SPEAKING,
        VoiceEventKind.IDLE,
    ]
    assert all(event.metadata["source"] == "external" for event in service.events)
    assert service.events[0].text == "hello。"
    service.speech.close()


def test_be_t06_wait_false_keeps_fifo_without_gateway_dependencies() -> None:
    backend = FakeBackend()
    service = _service(backend)

    service.speak_message("first", metadata={"message_id": "1"}, wait=False)
    service.speak_message("second", metadata={"message_id": "2"}, wait=False)
    assert service.speech.wait_done(1.0)

    assert backend.spoken == ["first。", "second。"]
    assert [event.kind for event in service.events] == [
        VoiceEventKind.SPEAKING,
        VoiceEventKind.IDLE,
        VoiceEventKind.SPEAKING,
        VoiceEventKind.IDLE,
    ]
    assert [event.metadata["message_id"] for event in service.events] == ["1", "1", "2", "2"]
    service.speech.close()


def test_be_t06_stop_speaking_interrupts_and_emits_one_idle() -> None:
    backend = FakeBackend(block_until_stop=True)
    service = _service(backend)
    service.speak_message("blocking", wait=False)
    assert backend.started.wait(1.0)

    service.stop_speaking()

    assert [event.kind for event in service.events] == [
        VoiceEventKind.SPEAKING,
        VoiceEventKind.IDLE,
    ]
    assert service.events[-1].metadata["source"] == "stop_speaking"
    service.speech.close()


def test_be_t06_wait_true_propagates_backend_error_after_idle() -> None:
    backend = FakeBackend(fail=True)
    service = _service(backend)

    with pytest.raises(RuntimeError, match="speech failed"):
        service.speak_message("hello", wait=True)

    assert [event.kind for event in service.events] == [
        VoiceEventKind.SPEAKING,
        VoiceEventKind.IDLE,
    ]
    service.speech.close()


def test_be_t06_empty_message_is_rejected() -> None:
    service = _service(FakeBackend())
    with pytest.raises(ValueError, match="text must not be empty"):
        service.speak_message("   ")
    service.speech.close()
