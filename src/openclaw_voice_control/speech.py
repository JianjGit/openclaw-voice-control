from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from .events import VoiceEvent, VoiceEventKind
from .runtime import RuntimeControl
from .text import clean_text_for_tts


class SpeechBackend(Protocol):
    def open(self) -> None:
        ...

    def speak(self, text: str, should_stop: Callable[[], bool]) -> bool:
        ...

    def close(self) -> None:
        ...


CompletionCallback = Callable[[bool, BaseException | None], None]


@dataclass(slots=True)
class _SpeechItem:
    text: str
    metadata: Mapping[str, Any]
    on_complete: CompletionCallback | None = None
    completion: threading.Event = field(default_factory=threading.Event)
    success: bool | None = None
    error: BaseException | None = None


_STOP = object()


class SpeechController:
    """Own the real-time speech queue and its single long-lived worker."""

    def __init__(
        self,
        backend: SpeechBackend,
        runtime: RuntimeControl,
        emit: Callable[[VoiceEvent], None],
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.backend = backend
        self.runtime = runtime
        self._emit = emit
        self.logger = logger or logging.getLogger("openclaw.voice_control.speech")
        self._queue: queue.Queue[_SpeechItem | object] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        self._pending = 0
        self._pending_condition = threading.Condition()
        self._closed = False

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._closed:
                raise RuntimeError("SpeechController is closed")
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run_worker,
                name="openclaw-speech-worker",
                daemon=True,
            )
            self._worker.start()

    def clear_stop_request(self) -> None:
        self.runtime.clear_stop_speech()

    def is_stop_requested(self) -> bool:
        return self.runtime.is_stop_speech_requested()

    def enqueue(
        self,
        text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        clean_markdown: bool = True,
        on_complete: CompletionCallback | None = None,
    ) -> _SpeechItem | None:
        speak_text = clean_text_for_tts(text) if clean_markdown else text
        speak_text = speak_text.strip()
        if not speak_text:
            return None

        item = _SpeechItem(
            text=speak_text,
            metadata=dict(metadata or {}),
            on_complete=on_complete,
        )
        with self._pending_condition:
            self._pending += 1
        try:
            self._ensure_worker()
            self._queue.put(item)
        except Exception:
            self._complete(item, success=False)
            raise
        return item

    def speak(
        self,
        text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        clean_markdown: bool = True,
        timeout: float | None = None,
    ) -> bool:
        self.clear_stop_request()
        item = self.enqueue(text, metadata=metadata, clean_markdown=clean_markdown)
        if item is None:
            return True
        if not item.completion.wait(timeout):
            return False
        if item.error is not None:
            raise item.error
        return bool(item.success)

    def wait_done(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._pending_condition:
            while self._pending:
                if deadline is None:
                    self._pending_condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._pending_condition.wait(remaining)
            return True

    def stop(self, timeout: float | None = 2.0, *, emit_idle: bool = True) -> bool:
        self.runtime.request_stop_speech()
        self._drain_pending_queue()
        done = self.wait_done(timeout)
        if emit_idle and done:
            self._emit(
                VoiceEvent(
                    kind=VoiceEventKind.IDLE,
                    metadata={"source": "speech_stop"},
                )
            )
        return done

    def play_sound_async(self, sound_path: str) -> None:
        player = getattr(self.backend, "play_sound_async", None)
        if player is not None:
            player(sound_path)

    def close(self, timeout: float = 2.0) -> None:
        with self._worker_lock:
            if self._closed:
                return
            self._closed = True
        self.runtime.request_stop_speech()
        self._drain_pending_queue()
        worker = self._worker
        if worker is None:
            return
        self._queue.put(_STOP)
        worker.join(timeout)

    def _run_worker(self) -> None:
        current: _SpeechItem | None = None
        try:
            self.backend.open()
            while True:
                queued = self._queue.get()
                if queued is _STOP:
                    break
                assert isinstance(queued, _SpeechItem)
                current = queued
                if self.runtime.is_stop_speech_requested():
                    self._complete(current, success=False)
                    current = None
                    continue
                try:
                    self._emit(
                        VoiceEvent(
                            kind=VoiceEventKind.SPEAKING,
                            text=current.text,
                            metadata=current.metadata,
                        )
                    )
                    success = self.backend.speak(
                        current.text,
                        self.runtime.is_stop_speech_requested,
                    )
                    self._complete(current, success=success)
                except BaseException as exc:
                    self.logger.exception("Speech backend failed while speaking")
                    self._complete(current, success=False, error=exc)
                finally:
                    current = None
        except BaseException as exc:
            self.logger.exception("Speech worker failed to initialize")
            if current is not None and not current.completion.is_set():
                self._complete(current, success=False, error=exc)
            self._fail_pending_queue(exc)
        finally:
            try:
                self.backend.close()
            except Exception:
                self.logger.exception("Speech backend failed to close")

    def _drain_pending_queue(self) -> None:
        retained_stop = False
        while True:
            try:
                queued = self._queue.get_nowait()
            except queue.Empty:
                break
            if queued is _STOP:
                retained_stop = True
                continue
            assert isinstance(queued, _SpeechItem)
            self._complete(queued, success=False)
        if retained_stop:
            self._queue.put(_STOP)

    def _fail_pending_queue(self, exc: BaseException) -> None:
        while True:
            try:
                queued = self._queue.get_nowait()
            except queue.Empty:
                break
            if queued is _STOP:
                continue
            assert isinstance(queued, _SpeechItem)
            self._complete(queued, success=False, error=exc)

    def _complete(
        self,
        item: _SpeechItem,
        *,
        success: bool,
        error: BaseException | None = None,
    ) -> None:
        if item.completion.is_set():
            return
        item.success = success
        item.error = error
        if item.on_complete is not None:
            try:
                item.on_complete(success, error)
            except Exception:
                self.logger.exception("Speech completion callback failed")
        item.completion.set()
        with self._pending_condition:
            self._pending -= 1
            self._pending_condition.notify_all()
