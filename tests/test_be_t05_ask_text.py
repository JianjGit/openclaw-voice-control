from __future__ import annotations

import threading
import time

import pytest

from openclaw_voice_control.events import VoiceEvent, VoiceEventKind
from openclaw_voice_control.service import VoiceControlService


class FakeClient:
    def __init__(self, *, error: Exception | None = None, delay: float = 0.0) -> None:
        self.error = error
        self.delay = delay
        self.ask_calls: list[str] = []
        self.streaming_calls: list[str] = []
        self._active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def _enter(self) -> None:
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        if self.delay:
            time.sleep(self.delay)

    def _leave(self) -> None:
        with self._lock:
            self._active -= 1

    def ask(self, text: str) -> str:
        self.ask_calls.append(text)
        self._enter()
        try:
            if self.error:
                raise self.error
            return "full reply"
        finally:
            self._leave()

    def ask_streaming(self, text: str, on_sentence) -> str:
        self.streaming_calls.append(text)
        self._enter()
        try:
            if self.error:
                raise self.error
            on_sentence("first sentence.")
            on_sentence("second sentence.")
            return "first sentence. second sentence."
        finally:
            self._leave()


class FakeSpeech:
    def __init__(self, emit) -> None:
        self.emit = emit
        self.enqueued: list[str] = []
        self.clear_count = 0
        self.wait_count = 0

    def clear_stop_request(self) -> None:
        self.clear_count += 1

    def enqueue(self, text: str, *, metadata=None, clean_markdown=True):
        self.enqueued.append(text)
        self.emit(VoiceEvent(VoiceEventKind.SPEAKING, text=text, metadata=dict(metadata or {})))
        return object()

    def wait_done(self, timeout=None) -> bool:
        self.wait_count += 1
        return True


def _service(client: FakeClient):
    service = VoiceControlService.__new__(VoiceControlService)
    service.client = client
    service._turn_lock = threading.Lock()
    service.events = []
    service._emit = service.events.append
    service.speech = FakeSpeech(service._emit)
    return service


def test_be_t05_ask_text_streaming_event_order_and_metadata() -> None:
    service = _service(FakeClient())
    metadata = {"source": "test"}

    reply = service.ask_text(" hello ", speak=True, metadata=metadata)

    assert reply == "first sentence. second sentence."
    assert service.client.streaming_calls == ["hello"]
    assert service.speech.enqueued == ["first sentence.", "second sentence."]
    assert [event.kind for event in service.events] == [
        VoiceEventKind.THINKING,
        VoiceEventKind.SPEAKING,
        VoiceEventKind.SPEAKING,
        VoiceEventKind.REPLY,
        VoiceEventKind.IDLE,
    ]
    assert service.events[0].user_text == "hello"
    assert service.events[-2].text == reply
    assert all(dict(event.metadata).get("source") == "test" for event in service.events)


def test_be_t05_speak_false_never_uses_speech_queue() -> None:
    service = _service(FakeClient())

    reply = service.ask_text("hello", speak=False)

    assert reply == "full reply"
    assert service.client.ask_calls == ["hello"]
    assert service.client.streaming_calls == []
    assert service.speech.enqueued == []
    assert service.speech.clear_count == 0
    assert [event.kind for event in service.events] == [
        VoiceEventKind.THINKING,
        VoiceEventKind.REPLY,
        VoiceEventKind.IDLE,
    ]


def test_be_t05_empty_text_is_rejected_before_gateway() -> None:
    service = _service(FakeClient())
    with pytest.raises(ValueError, match="text must not be empty"):
        service.ask_text("   ")
    assert service.client.ask_calls == []
    assert service.client.streaming_calls == []


def test_be_t05_gateway_error_emits_error_idle_and_reraises() -> None:
    service = _service(FakeClient(error=RuntimeError("gateway down")))

    with pytest.raises(RuntimeError, match="gateway down"):
        service.ask_text("hello", speak=False, metadata={"source": "test"})

    assert [event.kind for event in service.events] == [
        VoiceEventKind.THINKING,
        VoiceEventKind.ERROR,
        VoiceEventKind.IDLE,
    ]
    assert service.events[1].metadata["stage"] == "gateway"
    assert service.events[1].metadata["source"] == "test"


def test_be_t05_turn_lock_serializes_concurrent_calls() -> None:
    client = FakeClient(delay=0.05)
    service = _service(client)
    threads = [
        threading.Thread(target=service.ask_text, args=(f"message-{index}",), kwargs={"speak": False})
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert client.max_active == 1
