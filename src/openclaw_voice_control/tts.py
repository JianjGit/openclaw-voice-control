from __future__ import annotations

import os
import queue
import re
import threading
from dataclasses import dataclass, field

from .config import TTSConfig
from .runtime import RuntimeControl
from .text import clean_text_for_tts


_SENTENCE_SPLIT = re.compile(r"([^。！？\n]+[。！？\n])")


@dataclass(slots=True)
class WindowsTTS:
    config: TTSConfig
    runtime: RuntimeControl
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _voice: object | None = field(default=None, init=False)
    _sentence_queue: "queue.Queue[str]" = field(default_factory=queue.Queue, init=False)
    _player_thread: threading.Thread | None = field(default=None, init=False)
    _player_ready: threading.Event = field(default_factory=threading.Event, init=False)

    def __post_init__(self) -> None:
        self._player_ready.set()
        self._init_voice()

    def _init_voice(self) -> None:
        try:
            import win32com.client

            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
            self._voice.Rate = 1
            self._voice.Volume = 100
            for voice in self._voice.GetVoices():
                if self.config.voice.lower() in voice.GetDescription().lower():
                    self._voice.Voice = voice
                    break
        except Exception:
            self._voice = None

    def clear_stop_request(self) -> None:
        self.runtime.clear_stop_speech()

    def request_stop(self) -> None:
        self.runtime.request_stop_speech()

    def is_stop_requested(self) -> bool:
        return self.runtime.is_stop_speech_requested()

    def stop_current_speech(self) -> None:
        self.runtime.request_stop_speech()
        if self._voice is not None:
            try:
                self._voice.Skip("Sentence", 100)
                self._voice.Speak("", 1)
            except Exception:
                pass

    def speak(self, text: str, clean_markdown: bool = True) -> bool:
        if not text or self._voice is None:
            return True

        speak_text = clean_text_for_tts(text) if clean_markdown else text
        if not speak_text.strip():
            return True

        self.stop_current_speech()
        self.clear_stop_request()

        parts = _SENTENCE_SPLIT.findall(speak_text)
        sentences = [sentence.strip() for sentence in parts if sentence.strip()]
        consumed = len("".join(parts))
        remainder = speak_text[consumed:].strip()
        if remainder:
            sentences.append(remainder)
        if not sentences:
            sentences = [speak_text.strip()]

        import logging

        logger = logging.getLogger("openclaw.voice_control")
        logger.debug("TTS: %d sentences to speak", len(sentences))
        for index, sentence in enumerate(sentences):
            logger.debug("TTS sentence %d/%d: [%s]", index + 1, len(sentences), sentence[:60])
            if self.is_stop_requested():
                logger.info("TTS: stop requested before sentence %d", index + 1)
                return False
            try:
                self._voice.Speak(sentence, 0)
                logger.debug("TTS sentence %d done", index + 1)
            except Exception as exc:
                logger.error("TTS Speak failed on sentence %d: %s", index + 1, exc)
        logger.info("TTS: all %d sentences finished", len(sentences))
        return True

    def enqueue(self, sentence: str) -> None:
        """Push a sentence to the playback queue. Non-blocking."""
        if not sentence or self._voice is None:
            return
        speak_text = clean_text_for_tts(sentence)
        if not speak_text.strip():
            return
        self._sentence_queue.put(speak_text.strip())
        if self._player_thread is None or not self._player_thread.is_alive():
            self._player_ready.clear()
            self._player_thread = threading.Thread(target=self._play_queue, daemon=True)
            self._player_thread.start()

    def _play_queue(self) -> None:
        """Dedicated single-threaded queue player. Runs until queue empty or stopped."""
        import logging

        logger = logging.getLogger("openclaw.voice_control")
        try:
            while True:
                if self.is_stop_requested():
                    while True:
                        try:
                            self._sentence_queue.get_nowait()
                        except queue.Empty:
                            break
                    break
                try:
                    sentence = self._sentence_queue.get(timeout=0.3)
                except queue.Empty:
                    break
                try:
                    assert self._voice is not None
                    self._voice.Speak(sentence, 0)
                    logger.debug("TTS queue spoke: [%s]", sentence[:40])
                except Exception as exc:
                    logger.error("TTS Speak failed in queue: %s", exc)
        finally:
            self._player_ready.set()

    def wait_done(self, timeout: float = 30.0) -> None:
        """Wait for the playback queue to be fully drained and spoken."""
        self._player_ready.wait(timeout)

    def play_sound_async(self, sound_path: str) -> None:
        if not sound_path or not os.path.exists(sound_path):
            return
        threading.Thread(
            target=lambda: self._play_sync(sound_path),
            daemon=True,
        ).start()

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
