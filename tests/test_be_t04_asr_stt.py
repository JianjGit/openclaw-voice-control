from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from openclaw_voice_control.service import VoiceControlService
from openclaw_voice_control.stt_server import STTServer


class SerialCheckASR:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def transcribe(self, path: str) -> str:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.05)
            return "ok"
        finally:
            with self._lock:
                self.active -= 1


def _service_with_fake_asr(asr) -> VoiceControlService:
    service = VoiceControlService.__new__(VoiceControlService)
    service.asr = asr
    service._asr_lock = threading.Lock()
    return service


def test_be_t04_missing_file_is_explicit_error(tmp_path) -> None:
    service = _service_with_fake_asr(SerialCheckASR())
    with pytest.raises(FileNotFoundError):
        service.transcribe_file(tmp_path / "missing.wav")


def test_be_t04_direct_transcriptions_share_one_asr_lock(tmp_path) -> None:
    asr = SerialCheckASR()
    service = _service_with_fake_asr(asr)
    paths = [tmp_path / "one.wav", tmp_path / "two.wav"]
    for path in paths:
        path.write_bytes(b"wav")

    threads = [threading.Thread(target=service.transcribe_file, args=(path,)) for path in paths]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert asr.max_active == 1


def test_be_t04_http_contract_and_shared_lock(tmp_path) -> None:
    asr = SerialCheckASR()
    service = _service_with_fake_asr(asr)
    wav_path = tmp_path / "input.wav"
    wav_path.write_bytes(b"wav")
    server = STTServer(service.transcribe_file, port=0)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.bound_port}/stt"
        request = urllib.request.Request(
            url,
            data=json.dumps({"path": str(wav_path)}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        http_result: list[str] = []

        def call_http() -> None:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                http_result.append(json.loads(response.read())["text"])

        http_thread = threading.Thread(target=call_http)
        direct_thread = threading.Thread(target=service.transcribe_file, args=(wav_path,))
        http_thread.start()
        direct_thread.start()
        http_thread.join()
        direct_thread.join()

        assert http_result == ["ok"]
        assert asr.max_active == 1

        bad_request = urllib.request.Request(
            url,
            data=json.dumps({"path": str(tmp_path / "missing.wav")}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(bad_request, timeout=2.0)
        assert exc_info.value.code == 400
    finally:
        server.close()
