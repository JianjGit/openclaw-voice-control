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


_VALID_INPUT_MODES = frozenset({"wakeword", "push_to_talk"})


class VoiceControlService:
    def __init__(self, config: VoiceControlConfig, *, presenter: Presenter | None = None):
        self.config = config
        self.config.app.log_dir.mkdir(parents=True, exist_ok=True)

        self.logger = self._build_logger()
        self.presenter: Presenter = presenter or NullPresenter()
        self.runtime = RuntimeControl()
        self.client = OpenClawClient(config.openclaw)
        self.tts = WindowsTTS(config.tts)
        self.speech = SpeechController(self.tts, self.runtime, self._emit, logger=self.logger)
        self.asr = FunASRSenseVoice(config.asr)
        self._asr_lock = threading.Lock()
        self._turn_lock = threading.Lock()

        # _input_mode_lock serializes actual microphone ownership for both the
        # wakeword path and listen_once(). Mode state itself uses a condition so
        # run() can remain alive while push-to-talk mode is selected.
        self._input_mode_lock = threading.Lock()
        self._input_mode_condition = threading.Condition(threading.RLock())
        self._input_mode_apply_lock = threading.RLock()
        self._wakeword_io_lock = threading.Lock()
        self._input_mode = "wakeword"
        self._pending_input_mode: str | None = None
        self._active_operations = 0
        self._service_running = False
        self._service_ready = False
        self._wakeword_started = False
        self._wakeword_listening = False

        self._close_lock = threading.Lock()
        self._closed = False
        self.stt_server = STTServer(
            self.transcribe_file,
            host=config.stt.host,
            port=config.stt.port,
            logger=self.logger,
        )
        self.wakeword = build_wakeword_engine(config.wakeword)

    @staticmethod
    def _normalize_input_mode(mode: str) -> str:
        normalized = str(mode).strip().lower()
        if normalized not in _VALID_INPUT_MODES:
            raise ValueError("mode must be 'wakeword' or 'push_to_talk'")
        return normalized

    def get_input_mode(self) -> str:
        """Return the currently applied microphone input mode."""
        with self._input_mode_condition:
            return self._input_mode

    def get_pending_input_mode(self) -> str | None:
        """Return a queued mode change, if the current activity must finish first."""
        with self._input_mode_condition:
            return self._pending_input_mode

    def set_input_mode(self, mode: str) -> bool:
        """Switch microphone input mode without rebuilding the service.

        Returns True when the requested mode is applied before this method
        returns. Returns False when the change is queued until the current
        recording / ASR / Gateway / TTS activity releases the voice pipeline.
        """
        target = self._normalize_input_mode(mode)
        with self._input_mode_apply_lock:
            with self._input_mode_condition:
                if self._closed:
                    raise RuntimeError("VoiceControlService is closed")
                if target == self._input_mode:
                    self._pending_input_mode = None
                    self._input_mode_condition.notify_all()
                    return True
                self._pending_input_mode = target
                if self._active_operations:
                    self.logger.info(
                        "Input mode change queued until current activity finishes | current=%s target=%s",
                        self._input_mode,
                        target,
                    )
                    self._input_mode_condition.notify_all()
                    return False

            applied = self._apply_pending_input_mode_if_idle()
            if not applied:
                self.logger.info(
                    "Input mode change queued until microphone is released | current=%s target=%s",
                    self.get_input_mode(),
                    target,
                )
            return applied

    def _begin_activity(self) -> None:
        condition = getattr(self, "_input_mode_condition", None)
        if condition is None:
            return
        with condition:
            self._active_operations += 1

    def _end_activity(self) -> None:
        condition = getattr(self, "_input_mode_condition", None)
        if condition is None:
            return
        with condition:
            self._active_operations -= 1
            if self._active_operations < 0:
                self._active_operations = 0
                raise RuntimeError("voice activity counter underflow")
            should_apply = self._active_operations == 0 and self._pending_input_mode is not None
            condition.notify_all()
        if should_apply:
            self._apply_pending_input_mode_if_idle()

    def _set_wakeword_listening(self, enabled: bool) -> None:
        with self._wakeword_io_lock:
            if enabled:
                if not self._wakeword_started:
                    self._start_wakeword_engine()
                elif not self._wakeword_listening:
                    self.wakeword.resume()
                    self._wakeword_listening = True
                return

            if self._wakeword_started and self._wakeword_listening:
                self.wakeword.pause()
                self._wakeword_listening = False

    def _apply_pending_input_mode_if_idle(self) -> bool:
        apply_lock = getattr(self, "_input_mode_apply_lock", None)
        if apply_lock is None:
            return True
        with apply_lock:
            condition = getattr(self, "_input_mode_condition", None)
            if condition is None:
                return True

            with condition:
                target = self._pending_input_mode
                if target is None:
                    return True
                if self._active_operations:
                    return False

            # The same lock is used by wakeword reads/turns and listen_once(), so
            # mode application can never open/resume a microphone while another
            # input path owns it.
            if not self._input_mode_lock.acquire(timeout=0.25):
                return False

            try:
                with condition:
                    target = self._pending_input_mode
                    if target is None:
                        return True
                    if self._active_operations:
                        return False
                    service_running = self._service_running

                if service_running:
                    self._set_wakeword_listening(target == "wakeword")

                with condition:
                    # A newer request may have replaced this target while wakeword
                    # I/O was being synchronized. Only commit the target we applied.
                    if self._pending_input_mode != target:
                        return False
                    self._input_mode = target
                    self._pending_input_mode = None
                    condition.notify_all()
            finally:
                self._input_mode_lock.release()

            self.logger.info("Input mode changed | mode=%s", target)
            self._emit(
                VoiceEvent(
                    kind=VoiceEventKind.IDLE,
                    metadata={
                        "source": "input_mode",
                        "input_mode": target,
                        "mode_change": "applied",
                    },
                )
            )
            return True

    def _start_wakeword_engine(self) -> None:
        if self._wakeword_started:
            if not self._wakeword_listening:
                self.wakeword.resume()
                self._wakeword_listening = True
            return

        self.logger.info("Initializing wakeword engine...")
        self.wakeword.start()
        self._wakeword_started = True
        self._wakeword_listening = True
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

        self._begin_activity()
        try:
            with self._asr_lock:
                return self.asr.transcribe(str(audio_path))
        finally:
            self._end_activity()

    def listen_once(
        self,
        *,
        speak: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        """Run one push-to-talk voice turn without waiting for a wakeword."""
        condition = getattr(self, "_input_mode_condition", None)
        if condition is not None:
            with condition:
                if self._service_running and not self._service_ready:
                    raise RuntimeError("Voice service is not ready")
                if self._service_running and self._input_mode != "push_to_talk":
                    raise RuntimeError("listen_once requires push_to_talk input mode while run() is active")

        if not self._input_mode_lock.acquire(blocking=False):
            raise RuntimeError("voice input is already active")

        activity_started = False
        event_metadata = {"source": "listen_once", **dict(metadata or {})}
        try:
            if condition is not None:
                with condition:
                    if self._service_running and not self._service_ready:
                        raise RuntimeError("Voice service is not ready")
                    if self._service_running and self._input_mode != "push_to_talk":
                        raise RuntimeError(
                            "listen_once requires push_to_talk input mode while run() is active"
                        )
            self._begin_activity()
            activity_started = True
            self.speech.clear_stop_request()
            try:
                wav_path = self.record_until_silence(metadata=event_metadata)
            except Exception as exc:
                self.logger.exception("Recording failed during listen_once")
                self._emit(
                    VoiceEvent(
                        kind=VoiceEventKind.ERROR,
                        text=str(exc),
                        metadata={
                            **event_metadata,
                            "stage": "recording",
                            "exception_type": type(exc).__name__,
                            "recoverable": True,
                        },
                    )
                )
                self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))
                raise

            if not wav_path:
                return False
            return self.handle_one_turn(
                wav_path,
                speak=speak,
                metadata=event_metadata,
            )
        finally:
            self._input_mode_lock.release()
            if activity_started:
                self._end_activity()
            else:
                self._apply_pending_input_mode_if_idle()

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

        self._begin_activity()
        try:
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
        finally:
            self._end_activity()

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
        self._begin_activity()
        completion_releases_activity = False

        def on_complete(success: bool, error: BaseException | None) -> None:
            del success, error
            try:
                if not self.runtime.is_stop_speech_requested():
                    self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))
            finally:
                self._end_activity()

        try:
            item = self.speech.enqueue(
                message,
                metadata=event_metadata,
                on_complete=on_complete,
            )
            if item is None:
                self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))
                return
            completion_releases_activity = True
            if wait:
                item.completion.wait()
                if item.error is not None:
                    raise item.error
        finally:
            if not completion_releases_activity:
                self._end_activity()

    def stop_speaking(self) -> None:
        self.speech.stop(timeout=None, emit_idle=False)
        self._emit(
            VoiceEvent(
                kind=VoiceEventKind.IDLE,
                metadata={"source": "stop_speaking"},
            )
        )
        self._apply_pending_input_mode_if_idle()

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        self.runtime.request_shutdown()
        condition = getattr(self, "_input_mode_condition", None)
        if condition is not None:
            with condition:
                condition.notify_all()

        self.stt_server.close()
        self.speech.close()
        try:
            with self._wakeword_io_lock:
                self.wakeword.close()
                self._wakeword_started = False
                self._wakeword_listening = False
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

    def record_until_silence(
        self,
        prepared_stream: sd.InputStream | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Optional[str]:
        audio = self.config.audio
        event_metadata = {"source": "wakeword", **dict(metadata or {})}
        self.logger.info("Start listening")
        self._emit(
            VoiceEvent(
                kind=VoiceEventKind.LISTENING,
                text="请开始说话",
                metadata=event_metadata,
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
            if prepared_stream is None:
                stream.start()
            while total_time < audio.max_record_seconds:
                if self.speech.is_stop_requested():
                    self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))
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
                                        **event_metadata,
                                        "stage": "recording",
                                        "recoverable": True,
                                    },
                                )
                            )
                            if self.config.tts.no_speech_beep_enabled:
                                self.speech.play_sound_async(self.config.tts.no_speech_sound)
                            self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))
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
                            **event_metadata,
                            "stage": "recording",
                            "recoverable": True,
                        },
                    )
                )
                if self.config.tts.no_speech_beep_enabled:
                    self.speech.play_sound_async(self.config.tts.no_speech_sound)
                self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata=event_metadata))
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

    def handle_one_turn(
        self,
        wav_path: str,
        *,
        speak: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        event_metadata = {"source": "wakeword", **dict(metadata or {})}
        try:
            try:
                user_text = self.transcribe_file(wav_path, metadata=event_metadata)
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
                reply = self.ask_text(user_text, speak=speak, metadata=event_metadata)
            except Exception:
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
        with self._input_mode_condition:
            if self._service_running:
                raise RuntimeError("VoiceControlService.run() is already active")
            self._service_running = True
            self._service_ready = False
            self._input_mode_condition.notify_all()

        try:
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
            with self._input_mode_condition:
                self._service_running = False
                self._service_ready = False
                self._input_mode_condition.notify_all()
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

        if self.get_input_mode() == "wakeword":
            if not self._input_mode_lock.acquire(blocking=False):
                raise RuntimeError("voice input is already active")
            try:
                self._set_wakeword_listening(True)
            finally:
                self._input_mode_lock.release()

        with self._input_mode_condition:
            self._service_ready = True
            self._input_mode_condition.notify_all()

        self.speech.clear_stop_request()
        self._emit(
            VoiceEvent(
                kind=VoiceEventKind.IDLE,
                metadata={"source": "startup", "input_mode": self.get_input_mode()},
            )
        )
        self.logger.info("Voice service ready | input_mode=%s", self.get_input_mode())

        next_allowed_trigger_time = 0.0
        wakeword_armed = True

        def extend_rearm(seconds: float) -> None:
            nonlocal next_allowed_trigger_time
            next_allowed_trigger_time = max(next_allowed_trigger_time, time.time() + seconds)

        while not self.runtime.is_shutdown_requested():
            self._apply_pending_input_mode_if_idle()

            with self._input_mode_condition:
                if self._input_mode != "wakeword":
                    self._input_mode_condition.wait(timeout=0.1)
                    continue

            if not self._input_mode_lock.acquire(timeout=0.1):
                continue

            activity_started = False
            try:
                with self._input_mode_condition:
                    if self._input_mode != "wakeword":
                        continue
                    if self._pending_input_mode == "push_to_talk":
                        continue

                with self._wakeword_io_lock:
                    if not self._wakeword_started:
                        self._start_wakeword_engine()
                    elif not self._wakeword_listening:
                        self.wakeword.resume()
                        self._wakeword_listening = True
                    _, keyword_index = self.wakeword.read()

                if not wakeword_armed:
                    continue
                if keyword_index < 0:
                    continue

                now = time.time()
                if now < next_allowed_trigger_time:
                    continue

                # Atomically claim the current turn before a mode switch can be
                # applied. Any request after this point becomes pending.
                with self._input_mode_condition:
                    if self._input_mode != "wakeword":
                        continue
                    if self._pending_input_mode == "push_to_talk":
                        continue
                    self._active_operations += 1
                    activity_started = True

                next_allowed_trigger_time = now + self.config.wakeword.cooldown_seconds
                wakeword_armed = False
                self.logger.info("Wakeword detected")

                # Keep the wakeword model loaded but close its microphone for
                # the entire recorded conversation, including ASR/Gateway/TTS.
                with self._wakeword_io_lock:
                    if self._wakeword_started and self._wakeword_listening:
                        self.wakeword.pause()
                        self._wakeword_listening = False

                self.speech.clear_stop_request()
                wake_ok = self.speech.speak(
                    self.config.tts.wake_ack,
                    metadata={"source": "wakeword_ack"},
                    clean_markdown=False,
                )
                if not wake_ok or self.speech.is_stop_requested():
                    self._emit(VoiceEvent(kind=VoiceEventKind.IDLE, metadata={"source": "wakeword"}))
                    self.speech.clear_stop_request()
                    continue

                block_size = int(self.config.audio.sample_rate * 0.1)
                prepared_stream = self._build_record_stream(block_size)
                wav_path: Optional[str] = None
                try:
                    prepared_stream.start()
                    wav_path = self.record_until_silence(prepared_stream=prepared_stream)
                except Exception as exc:
                    self.logger.exception("Failed to hand off from wakeword to recording")
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

                if not wav_path:
                    extend_rearm(self.config.wakeword.rearm_seconds_after_turn)
                    wakeword_armed = True
                    self.speech.clear_stop_request()
                    continue

                self.handle_one_turn(wav_path)
                extend_rearm(self.config.wakeword.rearm_seconds_after_turn)
                wakeword_armed = True
            finally:
                if activity_started:
                    with self._input_mode_condition:
                        should_resume_wakeword = (
                            not self.runtime.is_shutdown_requested()
                            and self._input_mode == "wakeword"
                            and self._pending_input_mode is None
                        )
                    if should_resume_wakeword:
                        self._set_wakeword_listening(True)

                self._input_mode_lock.release()
                if activity_started:
                    self._end_activity()
                else:
                    self._apply_pending_input_mode_if_idle()