from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import wave
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping, Optional

import numpy as np
import sounddevice as sd

from .asr import FunASRSenseVoice
from .config import VoiceControlConfig
from .events import VoiceEvent, VoiceEventKind
from .openclaw_client import OpenClawClient
from .presenter import NullPresenter, Presenter
from .runtime import RuntimeControl
from .speech import SpeechController
from .stt_server import STTServer
from .tts import WindowsTTS
from .wakeword import build_wakeword_engine


class VoiceControlService:
    def __init__(self, config: VoiceControlConfig, *, presenter: Presenter | None = None):
        self.config = config
        self.config.app.log_dir.mkdir(parents=True, exist_ok=True)
        self.config.app.runtime_dir.mkdir(parents=True, exist_ok=True)

        self.logger = self._build_logger()
        self.presenter: Presenter = presenter or NullPresenter()
        self.runtime = RuntimeControl()
        self.client = OpenClawClient(config.openclaw)
        self.tts = WindowsTTS(config.tts)
        self.speech = SpeechController(self.tts, self.runtime, self._emit, logger=self.logger)
        self.asr = FunASRSenseVoice(config.asr)
        self._asr_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False
        self.stt_server = STTServer(self.transcribe_file, logger=self.logger)
        self.wakeword = build_wakeword_engine(config.wakeword)

    def _start_wakeword_engine(self) -> None:
        self.logger.info("Initializing wakeword engine...")
        self.wakeword.start()
        if self.config.wakeword.provider.strip().lower() == "openwakeword":
            self.logger.info(
                "Wakeword engine ready | provider=%s model_name=%s model_path=%s threshold=%.2f",
                self.config.wakeword.provider,
                self.config.wakeword.model_name,
                self.config.wakeword.model_path,
                self.config.wakeword.threshold,
            )
        else:
            self.logger.info(
                "Wakeword engine ready | provider=%s keyword_path=%s",
                self.config.wakeword.provider,
                self.config.wakeword.keyword_path,
            )

    def _build_logger(self) -> logging.Logger:
        logger = logging.getLogger("openclaw.voice_control")
        if logger.handlers:
            return logger

        logger.setLevel(getattr(logging, self.config.app.log_level, logging.INFO))
        logger.propagate = False
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        file_handler = RotatingFileHandler(
            self.config.app.log_dir / "voice_control.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        return logger

    def _emit(self, event: VoiceEvent) -> None:
        try:
            self.presenter.emit(event)
        except Exception:
            self.logger.exception("Presenter failed while handling %s", event.kind.value)

    def transcribe_file(
        self,
        path: str | Path,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        del metadata
        audio_path = Path(path).expanduser()
        if not audio_path.is_file():
            raise FileNotFoundError(str(audio_path))
        with self._asr_lock:
            return self.asr.transcribe(str(audio_path))

    def ask_text(
        self,
        text: str,
        *,
        speak: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        user_text = text.strip()
        if not user_text:
            raise ValueError("text must not be empty")

        event_metadata = dict(metadata or {})
        with self._turn_lock:
            if speak:
                self.speech.clear_stop_request()
            self._emit(
                VoiceEvent(
                    kind=VoiceEventKind.THINKING,
                    user_text=user_text,
                    metadata=event_metadata,
                )
            )
            try:
                if speak:
                    def on_sentence(sentence: str) -> None:
                        self.speech.enqueue(sentence, metadata=event_metadata)

                    reply = self.client.ask_streaming(user_text, on_sentence=on_sentence)
                else:
                    reply = self.client.ask(user_text)
            except Exception as exc:
                self._emit(
                    VoiceEvent(
                        kind=VoiceEventKind.ERROR,
                        text=str(exc),
                        user_text=user_text,
                        metadata={
                            **event_metadata,
                            "stage": "gateway",
                            "exception_type": type(exc).__name__,
                            "recoverable": True,
                        },
                    )
                )
                self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))
                raise

            self._emit(
                VoiceEvent(
                    kind=VoiceEventKind.REPLY,
                    text=reply,
                    user_text=user_text,
                    metadata=event_metadata,
                )
            )
            if speak:
                self.speech.wait_done()
            self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))
            return reply

    def speak_message(
        self,
        text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        wait: bool = False,
    ) -> None:
        message = text.strip()
        if not message:
            raise ValueError("text must not be empty")
        event_metadata = dict(metadata or {})
        self.speech.clear_stop_request()

        def on_complete(success: bool, error: BaseException | None) -> None:
            del success, error
            if not self.runtime.is_stop_speech_requested():
                self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))

        item = self.speech.enqueue(
            message,
            metadata=event_metadata,
            on_complete=on_complete,
        )
        if item is None:
            self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))
            return
        if wait:
            item.completion.wait()
            if item.error is not None:
                raise item.error

    def stop_speaking(self) -> None:
        self.speech.stop(timeout=None, emit_idle=False)
        self._emit(
            VoiceEvent(
                kind=VoiceEventKind.IDLE,
                metadata={"source": "stop_speaking"},
            )
        )

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self.runtime.request_shutdown()
        self.stt_server.close()
        self.speech.close()
        try:
            self.wakeword.close()
        except Exception:
            self.logger.exception("Failed to close wakeword engine")
        self.client.close()

    @staticmethod
    def rms_level(audio_chunk: np.ndarray) -> float:
        chunk = audio_chunk.astype("float32")
        if chunk.ndim > 1:
            chunk = chunk[:, 0]
        chunk /= 32768.0
        return float((chunk * chunk).mean() ** 0.5)

    def _build_record_stream(self, block_size: int) -> sd.InputStream:
        audio = self.config.audio
        return sd.InputStream(
            samplerate=audio.sample_rate,
            channels=audio.channels,
            device=audio.input_device_index if audio.input_device_index >= 0 else None,
            dtype="int16",
            blocksize=block_size,
        )

    def record_until_silence(self, prepared_stream: sd.InputStream | None = None) -> Optional[str]:
        audio = self.config.audio
        self.logger.info("Start listening")
        self._emit(
            VoiceEvent(
                kind=VoiceEventKind.LISTENING,
                text="请开始说话",
                metadata={"source": "wakeword"},
            )
        )

        frames: list[np.ndarray] = []
        pending_frames: list[np.ndarray] = []
        speech_started = False
        silence_time = 0.0
        total_time = 0.0
        speech_time = 0.0
        wait_for_start_time = 0.0
        start_hit_count = 0

        block_duration = 0.1
        block_size = int(audio.sample_rate * block_duration)
        stream = prepared_stream or self._build_record_stream(block_size)

        try:
            # A prepared stream is explicitly already started by the wakeword handoff caller.
            if prepared_stream is None:
                stream.start()
            while total_time < audio.max_record_seconds:
                if self.speech.is_stop_requested():
                    self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata={"source": "recording"}))
                    return None

                data, _ = stream.read(block_size)
                total_time += block_duration
                level = self.rms_level(data)

                if not speech_started:
                    pending_frames.append(data.copy())
                    if len(pending_frames) > audio.max_pending_blocks:
                        pending_frames = pending_frames[-audio.max_pending_blocks :]

                    if level >= audio.start_hit_threshold:
                        start_hit_count += 1
                    else:
                        start_hit_count = 0
                        wait_for_start_time += block_duration
                        if wait_for_start_time >= audio.start_timeout_seconds:
                            self._emit(
                                VoiceEvent(
                                    kind=VoiceEventKind.ERROR,
                                    text="没有检测到有效语音",
                                    metadata={
                                        "source": "wakeword",
                                        "stage": "recording",
                                        "recoverable": True,
                                    },
                                )
                            )
                            if self.config.tts.no_speech_beep_enabled:
                                self.speech.play_sound_async(self.config.tts.no_speech_sound)
                            self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata={"source": "wakeword"}))
                            return None

                    if start_hit_count >= audio.start_hits_required:
                        speech_started = True
                        silence_time = 0.0
                        frames.extend(pending_frames)
                        speech_time += len(pending_frames) * block_duration
                        pending_frames = []
                else:
                    frames.append(data.copy())
                    if level >= audio.silence_threshold:
                        silence_time = 0.0
                        speech_time += block_duration
                    else:
                        silence_time += block_duration
                        if silence_time >= audio.silence_seconds_end:
                            break

            if not speech_started or speech_time < audio.min_speech_seconds:
                self._emit(
                    VoiceEvent(
                        kind=VoiceEventKind.ERROR,
                        text="没有检测到有效语音",
                        metadata={
                            "source": "wakeword",
                            "stage": "recording",
                            "recoverable": True,
                        },
                    )
                )
                if self.config.tts.no_speech_beep_enabled:
                    self.speech.play_sound_async(self.config.tts.no_speech_sound)
                self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata={"source": "wakeword"}))
                return None

            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)

            with wave.open(path, "wb") as wf:
                wf.setnchannels(audio.channels)
                wf.setsampwidth(2)
                wf.setframerate(audio.sample_rate)
                for frame in frames:
                    wf.writeframes(frame.tobytes())

            if self.config.tts.record_done_beep_enabled:
                self.speech.play_sound_async(self.config.tts.record_done_sound)
            return path
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                self.logger.exception("Failed to close input stream")

    def handle_one_turn(self, wav_path: str) -> bool:
        event_metadata = {"source": "wakeword"}
        try:
            try:
                user_text = self.transcribe_file(wav_path)
            except Exception as exc:
                self.logger.exception("ASR failed during recorded turn")
                self._emit(
                    VoiceEvent(
                        kind=VoiceEventKind.ERROR,
                        text=str(exc),
                        metadata={
                            **event_metadata,
                            "stage": "asr",
                            "exception_type": type(exc).__name__,
                            "recoverable": True,
                        },
                    )
                )
                self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))
                return False

            self.logger.info("ASR: %s", user_text[:100] if user_text else "(empty)")
            if not user_text:
                self._emit(
                    VoiceEvent(
                        kind=VoiceEventKind.ERROR,
                        text="没有识别出有效文本",
                        metadata={**event_metadata, "stage": "asr", "recoverable": True},
                    )
                )
                self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))
                return False

            self._emit(
                VoiceEvent(
                    kind=VoiceEventKind.RECOGNIZED,
                    text=user_text,
                    user_text=user_text,
                    metadata=event_metadata,
                )
            )
            try:
                reply = self.ask_text(user_text, speak=True, metadata=event_metadata)
            except Exception:
                # ask_text emits gateway error -> idle and preserves the exception for SDK callers.
                self.logger.exception("OpenClaw turn failed")
                return False
            self.logger.info("Reply: %s", reply[:100] if reply else "(empty)")
            return True
        finally:
            wav_file = Path(wav_path)
            if wav_file.exists():
                try:
                    wav_file.unlink()
                except Exception:
                    self.logger.exception("Failed to remove temp wav file: %s", wav_path)

    def run(self) -> None:
        try:
            self._run_service()
        except Exception:
            self.logger.exception("FATAL: service crashed, writing crash dump...")
            crash_log = self.config.app.log_dir / "crash.log"
            import traceback

            with open(str(crash_log), "w", encoding="utf-8") as f:
                f.write("=" * 60 + "\n")
                f.write("Voice Control Crash Report\n")
                f.write("=" * 60 + "\n")
                traceback.print_exc(file=f)
            self.logger.info("Crash dump written to %s", str(crash_log))
            raise
        finally:
            self.close()

    def _run_service(self) -> None:
        self.logger.info(
            "Starting voice control service | platform=%s asr_model=%s language=%s",
            self.config.app.platform,
            self.config.asr.model,
            self.config.asr.language,
        )
        self.logger.info("Loading ASR model...")
        self.asr.load()
        self.logger.info("ASR model ready")
        self.stt_server.start()
        self._start_wakeword_engine()
        self.speech.clear_stop_request()
        self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata={"source": "startup"}))
        self.logger.info("Entered idle listening loop")

        next_allowed_trigger_time = 0.0
        wakeword_armed = True

        def extend_rearm(seconds: float) -> None:
            nonlocal next_allowed_trigger_time
            next_allowed_trigger_time = max(next_allowed_trigger_time, time.time() + seconds)

        while not self.runtime.is_shutdown_requested():
            _, keyword_index = self.wakeword.read()
            if not wakeword_armed:
                continue
            if keyword_index < 0:
                continue

            now = time.time()
            if now < next_allowed_trigger_time:
                continue
            next_allowed_trigger_time = now + self.config.wakeword.cooldown_seconds
            wakeword_armed = False
            self.logger.info("Wakeword detected")

            self.speech.clear_stop_request()
            wake_ok = self.speech.speak(
                self.config.tts.wake_ack,
                metadata={"source": "wakeword_ack"},
                clean_markdown=False,
            )
            if not wake_ok or self.speech.is_stop_requested():
                wakeword_armed = True
                self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata={"source": "wakeword"}))
                self.speech.clear_stop_request()
                continue

            block_size = int(self.config.audio.sample_rate * 0.1)
            prepared_stream = self._build_record_stream(block_size)
            wav_path: Optional[str] = None
            try:
                self.wakeword.pause()
                prepared_stream.start()
                wav_path = self.record_until_silence(prepared_stream=prepared_stream)
            except Exception as exc:
                self.logger.exception("Failed to hand off from wakeword listening to recording")
                try:
                    prepared_stream.stop()
                    prepared_stream.close()
                except Exception:
                    self.logger.exception("Failed to close prepared recording stream after handoff failure")
                self._emit(
                    VoiceEvent(
                        kind=VoiceEventKind.ERROR,
                        text=str(exc),
                        metadata={
                            "source": "wakeword",
                            "stage": "recording",
                            "exception_type": type(exc).__name__,
                            "recoverable": True,
                        },
                    )
                )
                self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata={"source": "wakeword"}))
            finally:
                if not self.runtime.is_shutdown_requested():
                    self.wakeword.resume()

            if not wav_path:
                extend_rearm(self.config.wakeword.rearm_seconds_after_turn)
                wakeword_armed = True
                self.speech.clear_stop_request()
                continue

            self.handle_one_turn(wav_path)
            extend_rearm(self.config.wakeword.rearm_seconds_after_turn)
            wakeword_armed = True
