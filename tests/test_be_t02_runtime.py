from __future__ import annotations

import inspect
import threading

from openclaw_voice_control.runtime import RuntimeControl
import openclaw_voice_control.tts as tts_module


def test_be_t02_stop_clear_shutdown_are_idempotent() -> None:
    runtime = RuntimeControl()

    runtime.request_stop_speech()
    runtime.request_stop_speech()
    assert runtime.is_stop_speech_requested()

    runtime.clear_stop_speech()
    runtime.clear_stop_speech()
    assert not runtime.is_stop_speech_requested()

    runtime.request_shutdown()
    runtime.request_shutdown()
    assert runtime.is_shutdown_requested()


def test_be_t02_runtime_signals_are_safe_from_multiple_threads() -> None:
    runtime = RuntimeControl()
    threads = [threading.Thread(target=runtime.request_stop_speech) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert runtime.is_stop_speech_requested()


def test_be_t02_tts_stop_control_has_no_overlay_file_dependency() -> None:
    source = inspect.getsource(tts_module)
    assert "OverlayStateManager" not in source
    assert "stop_tts.flag" not in source
    assert "stop_flag_file" not in source
