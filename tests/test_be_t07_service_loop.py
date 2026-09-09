from __future__ import annotations

import inspect
from types import SimpleNamespace

import numpy as np

from openclaw_voice_control.events import VoiceEventKind
from openclaw_voice_control.service import VoiceControlService


class FakeLogger:
    def info(self, *_args, **_kwargs) -> None:
        pass

    def exception(self, *_args, **_kwargs) -> None:
        pass


class FakeSpeech:
    def is_stop_requested(self) -> bool:
        return False

    def play_sound_async(self, _path: str) -> None:
        pass


class PreparedStream:
    def __init__(self) -> None:
        self.start_count = 1  # caller already started it before handoff
        self.stop_count = 0
        self.close_count = 0

    def start(self) -> None:
        self.start_count += 1

    def read(self, block_size: int):
        return np.zeros((block_size, 1), dtype=np.int16), False

    def stop(self) -> None:
        self.stop_count += 1

    def close(self) -> None:
        self.close_count += 1


def test_be_t07_recorded_turn_emits_recognized_and_reuses_public_api(tmp_path) -> None:
    wav_path = tmp_path / "turn.wav"
    wav_path.write_bytes(b"wav")
    service = VoiceControlService.__new__(VoiceControlService)
    service.logger = FakeLogger()
    service.events = []
    service._emit = service.events.append
    service.transcribe_file = lambda path, metadata=None: "hello"
    calls = []

    def ask_text(text: str, *, speak: bool = True, metadata=None) -> str:
        calls.append((text, speak, dict(metadata or {})))
        return "reply"

    service.ask_text = ask_text

    assert service.handle_one_turn(str(wav_path)) is True
    assert calls == [("hello", True, {"source": "wakeword"})]
    assert [event.kind for event in service.events] == [VoiceEventKind.RECOGNIZED]
    assert service.events[0].text == "hello"
    assert service.events[0].user_text == "hello"
    assert not wav_path.exists()


def test_be_t07_asr_failure_emits_error_idle_and_keeps_turn_recoverable(tmp_path) -> None:
    wav_path = tmp_path / "turn.wav"
    wav_path.write_bytes(b"wav")
    service = VoiceControlService.__new__(VoiceControlService)
    service.logger = FakeLogger()
    service.events = []
    service._emit = service.events.append

    def fail_transcribe(_path, metadata=None):
        raise RuntimeError("asr failed")

    service.transcribe_file = fail_transcribe

    assert service.handle_one_turn(str(wav_path)) is False
    assert [event.kind for event in service.events] == [VoiceEventKind.ERROR, VoiceEventKind.IDLE]
    assert service.events[0].metadata["stage"] == "asr"
    assert service.events[0].metadata["recoverable"] is True
    assert not wav_path.exists()


def test_be_t07_prepared_recording_stream_is_not_started_twice() -> None:
    service = VoiceControlService.__new__(VoiceControlService)
    service.logger = FakeLogger()
    service.events = []
    service._emit = service.events.append
    service.speech = FakeSpeech()
    service.config = SimpleNamespace(
        audio=SimpleNamespace(
            sample_rate=16000,
            channels=1,
            max_record_seconds=1.0,
            max_pending_blocks=2,
            start_hit_threshold=1.0,
            start_timeout_seconds=0.1,
            start_hits_required=1,
            silence_threshold=0.1,
            silence_seconds_end=0.2,
            min_speech_seconds=0.1,
        ),
        tts=SimpleNamespace(
            no_speech_beep_enabled=False,
            no_speech_sound="",
            record_done_beep_enabled=False,
            record_done_sound="",
        ),
    )
    stream = PreparedStream()

    assert service.record_until_silence(prepared_stream=stream) is None
    assert stream.start_count == 1
    assert stream.stop_count == 1
    assert stream.close_count == 1
    assert service.events[0].kind == VoiceEventKind.LISTENING
    assert service.events[-1].kind == VoiceEventKind.IDLE


def test_be_t07_service_main_loop_uses_presenter_and_pause_resume_handoff() -> None:
    source = inspect.getsource(VoiceControlService)
    loop_source = inspect.getsource(VoiceControlService._run_service)

    assert "OverlayStateManager" not in source
    assert "update_overlay_state" not in source
    assert "wakeword.pause()" in loop_source
    assert "wakeword.resume()" in loop_source
    assert "wakeword.close()" not in loop_source
