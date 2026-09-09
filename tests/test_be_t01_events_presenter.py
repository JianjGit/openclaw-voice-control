from __future__ import annotations

import io
import logging
import sys
from types import SimpleNamespace

from openclaw_voice_control import (
    ConsolePresenter,
    NullPresenter,
    VoiceControlService,
    VoiceEvent,
    VoiceEventKind,
)
import openclaw_voice_control.service as service_module


class RecordingPresenter:
    def __init__(self) -> None:
        self.events: list[VoiceEvent] = []

    def emit(self, event: VoiceEvent) -> None:
        self.events.append(event)


class RaisingPresenter:
    def emit(self, event: VoiceEvent) -> None:
        raise RuntimeError("presenter failed")


class RecordingLogger:
    def __init__(self) -> None:
        self.exceptions: list[tuple[object, ...]] = []

    def exception(self, *args, **kwargs) -> None:
        self.exceptions.append(args)


def _config(tmp_path):
    return SimpleNamespace(
        app=SimpleNamespace(log_dir=tmp_path / "logs", log_level="INFO"),
        openclaw=object(),
        stt=SimpleNamespace(host="127.0.0.1", port=15900),
        tts=object(),
        asr=object(),
        wakeword=SimpleNamespace(provider="openwakeword"),
    )


def _stub_backends(monkeypatch) -> None:
    monkeypatch.setattr(service_module, "OpenClawClient", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(service_module, "WindowsTTS", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(service_module, "FunASRSenseVoice", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(service_module, "build_wakeword_engine", lambda *_args, **_kwargs: object())


def test_be_t01_event_kinds_are_stable() -> None:
    assert [kind.value for kind in VoiceEventKind] == [
        "listening",
        "recognized",
        "thinking",
        "reply",
        "speaking",
        "idle",
        "error",
    ]


def test_be_t01_voice_event_defaults() -> None:
    event = VoiceEvent(VoiceEventKind.IDLE)
    assert event.text == ""
    assert event.user_text == ""
    assert event.auto_hide_ms == 0
    assert dict(event.metadata) == {}


def test_be_t01_null_presenter_is_noop() -> None:
    NullPresenter().emit(VoiceEvent(VoiceEventKind.IDLE))


def test_be_t01_console_presenter_logs_event() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("be-t01-console")
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    ConsolePresenter(logger).emit(VoiceEvent(VoiceEventKind.RECOGNIZED, text="hello"))

    output = stream.getvalue()
    assert "recognized" in output
    assert "hello" in output


def test_be_t01_default_service_uses_null_presenter_without_pyside6(tmp_path, monkeypatch) -> None:
    _stub_backends(monkeypatch)
    sys.modules.pop("PySide6", None)

    service = VoiceControlService(_config(tmp_path))

    assert isinstance(service.presenter, NullPresenter)
    assert "PySide6" not in sys.modules


def test_be_t01_fake_presenter_receives_events(tmp_path, monkeypatch) -> None:
    _stub_backends(monkeypatch)
    presenter = RecordingPresenter()
    service = VoiceControlService(_config(tmp_path), presenter=presenter)
    event = VoiceEvent(VoiceEventKind.THINKING, user_text="hello")

    service._emit(event)

    assert presenter.events == [event]


def test_be_t01_presenter_exception_is_isolated() -> None:
    service = VoiceControlService.__new__(VoiceControlService)
    service.presenter = RaisingPresenter()
    service.logger = RecordingLogger()

    service._emit(VoiceEvent(VoiceEventKind.ERROR, text="boom"))

    assert service.logger.exceptions
