from __future__ import annotations

import os
import subprocess
import threading
import time
import wave
from dataclasses import dataclass, field
from typing import Callable

from .config import TTSConfig


def _play_sync(sound_path: str) -> None:
    """Project WAV playback path used by notification sounds and generated VITS WAVs."""
    try:
        import winsound

        winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        escaped = sound_path.replace("'", "''")
        subprocess.Popen(
            ["powershell", "-c", f"(New-Object Media.SoundPlayer '{escaped}').PlaySync()"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def play_sound_async(sound_path: str) -> None:
    """Preserve the existing fire-and-forget WAV playback behavior."""
    if not sound_path or not os.path.exists(sound_path):
        return
    threading.Thread(target=lambda: _play_sync(sound_path), daemon=True).start()


def play_wav_interruptible(sound_path: str, should_stop: Callable[[], bool]) -> bool:
    """Play a generated WAV through the same Windows audio path and wait for safe cleanup."""
    if not sound_path or not os.path.exists(sound_path):
        return False

    with wave.open(sound_path, "rb") as wav_file:
        frames = wav_file.getnframes()
        frame_rate = wav_file.getframerate()
    duration = frames / float(frame_rate) if frame_rate else 0.0

    try:
        import winsound

        winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        deadline = time.monotonic() + duration + 0.10
        while time.monotonic() < deadline:
            if should_stop():
                try:
                    winsound.PlaySound(None, 0)
                finally:
                    return False
            time.sleep(0.05)
        return True
    except Exception:
        escaped = sound_path.replace("'", "''")
        proc = subprocess.Popen(
            ["powershell", "-c", f"(New-Object Media.SoundPlayer '{escaped}').PlaySync()"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        while proc.poll() is None:
            if should_stop():
                proc.terminate()
                return False
            time.sleep(0.05)
        return proc.returncode == 0


@dataclass(slots=True)
class _WindowsSAPI:
    """Windows SAPI5 implementation shared by the public class and VITS fallback."""

    config: TTSConfig
    _voice: object | None = field(default=None, init=False)
    _pythoncom: object | None = field(default=None, init=False)

    def open(self) -> None:
        if self._voice is not None:
            return
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise RuntimeError("Windows SAPI5 requires pywin32") from exc

        pythoncom.CoInitialize()
        self._pythoncom = pythoncom
        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voice.Rate = 1
            voice.Volume = 100
            for candidate in voice.GetVoices():
                if self.config.voice.lower() in candidate.GetDescription().lower():
                    voice.Voice = candidate
                    break
            self._voice = voice
        except Exception:
            self._pythoncom = None
            pythoncom.CoUninitialize()
            raise

    def speak(self, text: str, should_stop: Callable[[], bool]) -> bool:
        if not text:
            return True
        if self._voice is None:
            raise RuntimeError("WindowsTTS.open() must be called in the speech worker before speak()")

        self._voice.Speak(text, 1)
        while True:
            if should_stop():
                try:
                    self._voice.Skip("Sentence", 100)
                    self._voice.Speak("", 1)
                finally:
                    return False
            if self._voice.WaitUntilDone(50):
                return True

    def close(self) -> None:
        self._voice = None
        pythoncom = self._pythoncom
        self._pythoncom = None
        if pythoncom is not None:
            pythoncom.CoUninitialize()

    def play_sound_async(self, sound_path: str) -> None:
        play_sound_async(sound_path)

    @staticmethod
    def _play_sync(sound_path: str) -> None:
        _play_sync(sound_path)


class WindowsTTS(_WindowsSAPI):
    """Backwards-compatible public SAPI class with optional provider dispatch.

    The default path still constructs this class exactly as before. When the config
    explicitly selects VITS, construction returns the process-lifetime VITS backend
    while keeping existing service wiring and direct WindowsTTS imports intact.
    """

    def __new__(cls, config: TTSConfig):
        provider = getattr(config, "provider", "windows_sapi").strip().lower()
        if provider == "windows_sapi":
            return super().__new__(cls)
        if provider == "vits":
            from .vits_backend import VITSTTS

            return VITSTTS(config, fallback_factory=lambda: _WindowsSAPI(config))
        raise ValueError(f"Unsupported TTS provider: {provider}")
