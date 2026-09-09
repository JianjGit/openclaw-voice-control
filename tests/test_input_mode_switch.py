from __future__ import annotations

import threading

import pytest

from openclaw_voice_control.events import VoiceEventKind
from openclaw_voice_control.service import VoiceControlService


class FakeLogger:
    def info(self, *_args, **_kwargs) -> None:
        pass

    def exception(self, *_args, **_kwargs) -> None:
        pass


class FakeWakeword:
    def __init__(self) -> None:
        self.start_count = 0
        self.pause_count = 0
        self.resume_count = 0
        self.close_count = 0

    def start(self) -> None:
        self.start_count += 1

    def pause(self) -> None:
        self.pause_count += 1

    def resume(self) -> None:
        self.resume_count += 1

    def read(self):
        return [], -1

    def close(self) -> None:
        self.close_count += 1


class FakeSpeech:
    def __init__(self) -> None:
        self.clear_count = 0

    def clear_stop_request(self) -> None:
        self.clear_count += 1


def make_service(*, mode: str = "wakeword") -> VoiceControlService:
    service = VoiceControlService.__new__(VoiceControlService)
    service.logger = FakeLogger()
    service.events = []
    service._emit = service.events.append
    service._closed = False
    service._input_mode_lock = threading.Lock()
    service._input_mode_condition = threading.Condition(threading.RLock())
    service._input_mode_apply_lock = threading.RLock()
    service._wakeword_io_lock = threading.Lock()
    service._input_mode = mode
    service._pending_input_mode = None
    service._active_operations = 0
    service._service_running = True
    service._service_ready = True
    service._wakeword_started = True
    service._wakeword_listening = mode == "wakeword"
    service.wakeword = FakeWakeword()
    service.speech = FakeSpeech()
    return service


def test_runtime_switch_pauses_and_resumes_same_wakeword_engine() -> None:
    service = make_service(mode="wakeword")
    wakeword = service.wakeword

    assert service.set_input_mode("push_to_talk") is True
    assert service.get_input_mode() == "push_to_talk"
    assert service.get_pending_input_mode() is None
    assert wakeword.pause_count == 1
    assert wakeword.close_count == 0

    assert service.set_input_mode("wakeword") is True
    assert service.get_input_mode() == "wakeword"
    assert wakeword.resume_count == 1
    assert wakeword.start_count == 0
    assert wakeword.close_count == 0

    mode_events = [
        event for event in service.events if event.metadata.get("source") == "input_mode"
    ]
    assert [event.kind for event in mode_events] == [VoiceEventKind.IDLE, VoiceEventKind.IDLE]
    assert [event.metadata["input_mode"] for event in mode_events] == [
        "push_to_talk",
        "wakeword",
    ]
    assert all(event.metadata["mode_change"] == "applied" for event in mode_events)


def test_switch_is_pending_until_current_activity_finishes() -> None:
    service = make_service(mode="wakeword")

    service._begin_activity()
    assert service.set_input_mode("push_to_talk") is False
    assert service.get_input_mode() == "wakeword"
    assert service.get_pending_input_mode() == "push_to_talk"
    assert service.wakeword.pause_count == 0

    service._end_activity()

    assert service.get_input_mode() == "push_to_talk"
    assert service.get_pending_input_mode() is None
    assert service.wakeword.pause_count == 1
    assert service.events[-1].metadata == {
        "source": "input_mode",
        "input_mode": "push_to_talk",
        "mode_change": "applied",
    }


def test_new_request_can_cancel_a_pending_switch() -> None:
    service = make_service(mode="wakeword")

    service._begin_activity()
    assert service.set_input_mode("push_to_talk") is False
    assert service.get_pending_input_mode() == "push_to_talk"

    assert service.set_input_mode("wakeword") is True
    assert service.get_pending_input_mode() is None
    service._end_activity()

    assert service.get_input_mode() == "wakeword"
    assert service.wakeword.pause_count == 0


def test_microphone_lock_delays_mode_application() -> None:
    service = make_service(mode="push_to_talk")
    assert service._input_mode_lock.acquire(blocking=False) is True
    try:
        assert service.set_input_mode("wakeword") is False
        assert service.get_input_mode() == "push_to_talk"
        assert service.get_pending_input_mode() == "wakeword"
        assert service.wakeword.resume_count == 0
    finally:
        service._input_mode_lock.release()

    assert service._apply_pending_input_mode_if_idle() is True
    assert service.get_input_mode() == "wakeword"
    assert service.wakeword.resume_count == 1


def test_listen_once_requires_push_to_talk_while_run_is_active() -> None:
    service = make_service(mode="wakeword")

    with pytest.raises(RuntimeError, match="requires push_to_talk"):
        service.listen_once()


def test_listen_once_and_wakeword_share_the_same_input_lock() -> None:
    service = make_service(mode="push_to_talk")
    calls = []

    def record_until_silence(*, metadata=None):
        assert service._input_mode_lock.locked()
        calls.append(("record", dict(metadata or {})))
        return "turn.wav"

    def handle_one_turn(path: str, *, speak: bool = True, metadata=None) -> bool:
        assert service._input_mode_lock.locked()
        calls.append(("turn", path, speak, dict(metadata or {})))
        return True

    service.record_until_silence = record_until_silence
    service.handle_one_turn = handle_one_turn

    assert service.listen_once(metadata={"request_id": "ptt-1"}) is True
    assert calls == [
        ("record", {"source": "listen_once", "request_id": "ptt-1"}),
        (
            "turn",
            "turn.wav",
            True,
            {"source": "listen_once", "request_id": "ptt-1"},
        ),
    ]
    assert service._input_mode_lock.acquire(blocking=False) is True
    service._input_mode_lock.release()


def test_invalid_input_mode_is_rejected() -> None:
    service = make_service()
    with pytest.raises(ValueError, match="wakeword.*push_to_talk"):
        service.set_input_mode("voice_activation")
