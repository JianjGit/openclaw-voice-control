from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Callable

from .config import TTSConfig


@dataclass(slots=True)
class WindowsTTS:
    """Windows SAPI5 backend.

    The backend must be opened, used, and closed by the same worker thread.
    It owns no queue and no UI/runtime state.
    """

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

        # Asynchronous SAPI playback lets the same COM-owning worker poll the
        # runtime stop signal and interrupt the active utterance without
        # invoking COM from another thread.
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
        if not sound_path or not os.path.exists(sound_path):
            return
        threading.Thread(target=lambda: self._play_sync(sound_path), daemon=True).start()

    @staticmethod
    def _play_sync(sound_path: str) -> None:
        try:
            import winsound

            winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            import subprocess

            subprocess.Popen(
                ["powershell", "-c", f"(New-Object Media.SoundPlayer '{sound_path}').PlaySync()"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
