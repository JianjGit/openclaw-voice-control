from __future__ import annotations

import threading

import pytest

from openclaw_voice_control.events import VoiceEventKind
from openclaw_voice_control.service import VoiceControlService


class FakeLogger:
    def exception(self, *_args, **_kwargs) -> None:
        pass

    def info(self, *_args, **_kwargs) -> None:
        pass


class FakeSpeech:
    def __init__(self) -> None:
        self.clear_count = 0

    def clear_stop_request(self) -> None:
        self.clear_count += 1


def make_service() -> VoiceControlService:
    service = VoiceControlService.__new__(VoiceControlService)
    service.logger = FakeLogger()
    service.speech = FakeSpeech()
    service._input_mode_lock = threading.Lock()
    service.events = []
    service._emit = service.events.append
    return service


def test_listen_once_bypasses_wakeword_and_runs_full_recorded_turn() -> None:
    service = make_service()
    calls = []

    def record_until_silence(*, metadata=None):
        calls.append(("record", dict(metadata or {})))
        return "turn.wav"

    def handle_one_turn(path: str, *, speak: bool = True, metadata=None) -> bool:
        calls.append(("turn", path, speak, dict(metadata or {})))
        return True

    service.record_until_silence = record_until_silence
    service.handle_one_turn = handle_one_turn

    assert not hasattr(service, "wakeword")
    assert service.listen_once(metadata={"request_id": "push-1"}) is True
    assert service.speech.clear_count == 1
    assert calls == [
        ("record", {"source": "listen_once", "request_id": "push-1"}),
        (
            "turn",
            "turn.wav",
            True,
            {"source": "listen_once", "request_id": "push-1"},
        ),
    ]


def test_listen_once_returns_false_when_no_valid_speech_is_recorded() -> None:
    service = make_service()
    service.record_until_silence = lambda *, metadata=None: None

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("handle_one_turn must not run without recorded audio")

    service.handle_one_turn = should_not_run

    assert service.listen_once(speak=False) is False


def test_listen_once_rejects_concurrent_voice_input() -> None:
    service = make_service()
    assert service._input_mode_lock.acquire(blocking=False) is True
    try:
        with pytest.raises(RuntimeError, match="voice input is already active"):
            service.listen_once()
    finally:
        service._input_mode_lock.release()


def test_handle_one_turn_forwards_listen_once_metadata_and_speak_flag(tmp_path) -> None:
    wav_path = tmp_path / "turn.wav"
    wav_path.write_bytes(b"wav")
    service = make_service()
    calls = []

    def transcribe_file(path, *, metadata=None):
        calls.append(("asr", str(path), dict(metadata or {})))
        return "你好"

    def ask_text(text: str, *, speak: bool = True, metadata=None) -> str:
        calls.append(("ask", text, speak, dict(metadata or {})))
        return "你好，我在。"

    service.transcribe_file = transcribe_file
    service.ask_text = ask_text

    metadata = {"source": "desktop_pet", "request_id": "push-2"}
    assert service.handle_one_turn(
        str(wav_path),
        speak=False,
        metadata=metadata,
    ) is True

    assert calls == [
        ("asr", str(wav_path), metadata),
        ("ask", "你好", False, metadata),
    ]
    assert [event.kind for event in service.events] == [VoiceEventKind.RECOGNIZED]
    assert service.events[0].metadata == metadata
    assert not wav_path.exists()


def test_listen_once_emits_recording_error_and_releases_input_lock() -> None:
    service = make_service()

    def fail_record(*, metadata=None):
        raise OSError("microphone busy")

    service.record_until_silence = fail_record

    with pytest.raises(OSError, match="microphone busy"):
        service.listen_once(metadata={"request_id": "push-3"})

    assert [event.kind for event in service.events] == [
        VoiceEventKind.ERROR,
        VoiceEventKind.IDLE,
    ]
    assert service.events[0].metadata["source"] == "listen_once"
    assert service.events[0].metadata["stage"] == "recording"
    assert service.events[0].metadata["recoverable"] is True
    assert service._input_mode_lock.acquire(blocking=False) is True
    service._input_mode_lock.release()
