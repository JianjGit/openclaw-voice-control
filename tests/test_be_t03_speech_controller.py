from __future__ import annotations

import threading
import time

from openclaw_voice_control.events import VoiceEventKind
from openclaw_voice_control.runtime import RuntimeControl
from openclaw_voice_control.speech import SpeechController


class FakeBackend:
    def __init__(self, *, block_until_stop: bool = False, fail_text: str | None = None) -> None:
        self.block_until_stop = block_until_stop
        self.fail_text = fail_text
        self.started = threading.Event()
        self.open_thread: int | None = None
        self.close_thread: int | None = None
        self.speak_threads: list[int] = []
        self.spoken: list[str] = []

    def open(self) -> None:
        self.open_thread = threading.get_ident()

    def speak(self, text: str, should_stop) -> bool:
        self.speak_threads.append(threading.get_ident())
        self.spoken.append(text)
        self.started.set()
        if text == self.fail_text:
            raise RuntimeError("synthetic backend failure")
        if self.block_until_stop:
            while not should_stop():
                time.sleep(0.005)
            return False
        return not should_stop()

    def close(self) -> None:
        self.close_thread = threading.get_ident()


def test_be_t03_fifo_and_worker_backend_lifecycle() -> None:
    backend = FakeBackend()
    events = []
    controller = SpeechController(backend, RuntimeControl(), events.append)
    controller.clear_stop_request()

    controller.enqueue("one", clean_markdown=False)
    controller.enqueue("two", clean_markdown=False)
    controller.enqueue("three", clean_markdown=False)

    assert controller.wait_done(1.0)
    assert backend.spoken == ["one", "two", "three"]
    speaking = [event.text for event in events if event.kind == VoiceEventKind.SPEAKING]
    assert speaking == ["one", "two", "three"]
    assert len(set(backend.speak_threads)) == 1
    assert backend.open_thread == backend.speak_threads[0]

    controller.close()
    assert backend.close_thread == backend.open_thread


def test_be_t03_stop_interrupts_current_and_drains_pending_queue() -> None:
    backend = FakeBackend(block_until_stop=True)
    events = []
    controller = SpeechController(backend, RuntimeControl(), events.append)
    controller.clear_stop_request()

    first = controller.enqueue("first", clean_markdown=False)
    second = controller.enqueue("second", clean_markdown=False)
    third = controller.enqueue("third", clean_markdown=False)
    assert first is not None and second is not None and third is not None
    assert backend.started.wait(1.0)

    assert controller.stop(timeout=1.0)
    assert controller.wait_done(0.1)
    assert backend.spoken == ["first"]
    assert first.success is False
    assert second.success is False
    assert third.success is False
    assert events[-1].kind == VoiceEventKind.IDLE

    controller.close()


def test_be_t03_wait_reports_timeout_for_active_speech() -> None:
    backend = FakeBackend(block_until_stop=True)
    controller = SpeechController(backend, RuntimeControl(), lambda _event: None)
    controller.clear_stop_request()
    controller.enqueue("blocking", clean_markdown=False)
    assert backend.started.wait(1.0)

    assert not controller.wait_done(0.01)
    assert controller.stop(timeout=1.0)
    controller.close()


def test_be_t03_worker_exception_completes_item_and_keeps_worker_usable() -> None:
    backend = FakeBackend(fail_text="bad")
    controller = SpeechController(backend, RuntimeControl(), lambda _event: None)
    controller.clear_stop_request()

    bad = controller.enqueue("bad", clean_markdown=False)
    good = controller.enqueue("good", clean_markdown=False)
    assert bad is not None and good is not None

    assert controller.wait_done(1.0)
    assert isinstance(bad.error, RuntimeError)
    assert good.success is True
    assert backend.spoken == ["bad", "good"]
    controller.close()
