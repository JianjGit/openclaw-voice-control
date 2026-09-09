from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import wave
from http.server import HTTPServer, BaseHTTPRequestHandler
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import numpy as np
import requests
import sounddevice as sd

from .asr import FunASRSenseVoice
from .config import VoiceControlConfig
from .events import VoiceEvent
from .openclaw_client import OpenClawClient
from .presenter import NullPresenter, Presenter
from .runtime import RuntimeControl
from .speech import SpeechController
from .state import OverlayStateManager
from .text import clean_text_for_overlay
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
        self.asr_lock = threading.Lock()
        self.wakeword = build_wakeword_engine(config.wakeword)
        self.state = OverlayStateManager(config.overlay)

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

    def _restart_wakeword_engine(self) -> None:
        try:
            self.wakeword.close()
        except Exception:
            self.logger.exception("Failed to close wakeword engine before restart")
        self._start_wakeword_engine()

    def _return_to_idle(self, auto_hide_ms: int = 200, clear_stop_flag: bool = False) -> None:
        self.update_overlay_state("idle", auto_hide_ms=auto_hide_ms)
        if clear_stop_flag:
            self.speech.clear_stop_request()

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
        """Emit a core event without allowing presenter failures to stop the service."""
        try:
            self.presenter.emit(event)
        except Exception:
            self.logger.exception("Presenter failed while handling %s", event.kind.value)

    def update_overlay_state(
        self,
        status: str,
        user_text: str = "",
        reply_text: str = "",
        meta_text: str = "",
        auto_hide_ms: int = 4000,
    ) -> None:
        self.state.write(
            status=status,
            user_text=user_text,
            reply_text=reply_text,
            meta_text=meta_text,
            auto_hide_ms=auto_hide_ms,
        )

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
        self.update_overlay_state("listening", meta_text="请开始说话", auto_hide_ms=0)

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
            stream.start()
            while total_time < audio.max_record_seconds:
                if self.speech.is_stop_requested():
                    self.update_overlay_state("idle", auto_hide_ms=120)
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
                            self.update_overlay_state("no_speech", meta_text="没有检测到有效语音", auto_hide_ms=2200)
                            if self.config.tts.no_speech_beep_enabled:
                                self.speech.play_sound_async(self.config.tts.no_speech_sound)
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
                self.update_overlay_state("no_speech", meta_text="没有检测到有效语音", auto_hide_ms=2200)
                if self.config.tts.no_speech_beep_enabled:
                    self.speech.play_sound_async(self.config.tts.no_speech_sound)
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
        try:
            user_text = self.asr.transcribe(wav_path)
            self.logger.info("ASR: %s", user_text[:100] if user_text else "(empty)")
            if not user_text:
                self.update_overlay_state("no_speech", meta_text="没有识别出有效文本", auto_hide_ms=2200)
                self.speech.clear_stop_request()
                return False

            self.update_overlay_state("recognized", user_text=user_text, meta_text="识别完成", auto_hide_ms=1800)
            self.update_overlay_state("thinking", user_text=user_text, meta_text="正在思考", auto_hide_ms=0)

            self.speech.clear_stop_request()

            def on_sentence(sentence: str) -> None:
                self.speech.enqueue(sentence)

            reply = self.client.ask_streaming(user_text, on_sentence=on_sentence)
            self.logger.info("Reply: %s", reply[:100] if reply else "(empty)")

            if self.speech.is_stop_requested():
                self.update_overlay_state("idle", auto_hide_ms=120)
                return False

            reply_clean = clean_text_for_overlay(reply)
            self.update_overlay_state("reply", user_text=user_text, reply_text=reply_clean, meta_text="Jarvis", auto_hide_ms=0)

            self.speech.wait_done()
            if reply:
                time.sleep(self.config.tts.post_reply_delay)

            self.update_overlay_state("idle", auto_hide_ms=120)
            self.speech.clear_stop_request()
            return True
        except requests.HTTPError:
            self.logger.exception("OpenClaw request failed")
            self.update_overlay_state("no_speech", meta_text="请求失败", auto_hide_ms=2200)
            self.speech.speak("请求失败。", clean_markdown=False)
            self.speech.clear_stop_request()
            return False
        except Exception:
            self.logger.exception("Unexpected error during turn handling")
            self.update_overlay_state("no_speech", meta_text="出了点问题", auto_hide_ms=2200)
            self.speech.speak("出了点问题。", clean_markdown=False)
            self.speech.clear_stop_request()
            return False
        finally:
            wav_file = Path(wav_path)
            if wav_file.exists():
                try:
                    wav_file.unlink()
                except Exception:
                    self.logger.exception("Failed to remove temp wav file: %s", wav_path)

    def _start_stt_http_server(self, host: str = "127.0.0.1", port: int = 15900) -> None:
        """启动 STT HTTP 接口（常驻守护线程），供 OpenClaw 的 tools.media.audio CLI 调用。"""
        service_ref = self

        class STTHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/stt":
                    self.send_error(404)
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(content_length)
                    data = json.loads(body)
                    audio_path = data.get("path", "")
                    if not audio_path or not os.path.isfile(audio_path):
                        self.send_error(400, "Missing or invalid path")
                        return
                    with service_ref.asr_lock:
                        text = service_ref.asr.transcribe(audio_path)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"))
                except Exception:
                    service_ref.logger.exception("STT HTTP error")
                    self.send_error(500)

            def log_message(self, format, *args):
                pass

        server = HTTPServer((host, port), STTHandler)
        service_ref.logger.info("STT HTTP server listening on http://%s:%d/stt", host, port)
        server.serve_forever()

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
            self.speech.close()
            self.client.close()

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
        stt_thread = threading.Thread(target=self._start_stt_http_server, daemon=True)
        stt_thread.start()
        self._start_wakeword_engine()
        self.speech.clear_stop_request()
        self.state.ensure_idle_state(reset=True)
        self.update_overlay_state("idle", auto_hide_ms=200)
        self.logger.info("Entered idle listening loop")

        next_allowed_trigger_time = 0.0
        wakeword_armed = True

        def extend_rearm(seconds: float) -> None:
            nonlocal next_allowed_trigger_time
            next_allowed_trigger_time = max(next_allowed_trigger_time, time.time() + seconds)

        while True:
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

            self.update_overlay_state("wake", meta_text="已唤醒", auto_hide_ms=1800)
            self.speech.clear_stop_request()
            wake_ok = self.speech.speak(self.config.tts.wake_ack, clean_markdown=False)
            if not wake_ok or self.speech.is_stop_requested():
                wakeword_armed = True
                self.update_overlay_state("idle", auto_hide_ms=120)
                self.speech.clear_stop_request()
                continue

            block_size = int(self.config.audio.sample_rate * 0.1)
            prepared_stream = self._build_record_stream(block_size)
            wav_path: Optional[str] = None
            try:
                self.wakeword.close()
                prepared_stream.start()
                wav_path = self.record_until_silence(prepared_stream=prepared_stream)
            except Exception:
                self.logger.exception("Failed to hand off from wakeword listening to recording")
                try:
                    prepared_stream.stop()
                    prepared_stream.close()
                except Exception:
                    self.logger.exception("Failed to close prepared recording stream after handoff failure")
                self._start_wakeword_engine()
                self.update_overlay_state("no_speech", meta_text="录音启动失败", auto_hide_ms=2200)
                continue
            if not wav_path:
                extend_rearm(self.config.wakeword.rearm_seconds_after_turn)
                wakeword_armed = True
                self._start_wakeword_engine()
                self._return_to_idle(auto_hide_ms=200, clear_stop_flag=True)
                continue

            self.handle_one_turn(wav_path)
            extend_rearm(self.config.wakeword.rearm_seconds_after_turn)
            wakeword_armed = True
            self._start_wakeword_engine()
            self._return_to_idle(auto_hide_ms=120)
