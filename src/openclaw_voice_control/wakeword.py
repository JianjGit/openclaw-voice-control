from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import sounddevice as sd

from .config import WakewordConfig


def _normalize_model_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _canonical_openwakeword_name(value: str, available: list[str]) -> str:
    requested = _normalize_model_name(value)
    if not requested:
        return available[0]

    for key in available:
        if _normalize_model_name(key) == requested:
            return key

    available_display = ", ".join(sorted(available))
    raise RuntimeError(
        f"openWakeWord model '{value}' is not available. Available models: {available_display}"
    )


class WakewordEngine(Protocol):
    def start(self) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def read(self) -> tuple[list[int], int]: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class PorcupineWakewordEngine:
    config: WakewordConfig
    _porcupine: Any | None = field(default=None, init=False)
    _recorder: Any | None = field(default=None, init=False)

    def start(self) -> None:
        if self._porcupine is None:
            if self.config.keyword_path is None:
                raise RuntimeError("WAKEWORD_FILE is required when wakeword.provider=porcupine.")
            if not self.config.access_key:
                raise RuntimeError("PICOVOICE_ACCESS_KEY is required when wakeword.provider=porcupine.")

            try:
                import pvporcupine
            except ImportError as exc:
                raise RuntimeError("Porcupine dependencies are not installed.") from exc

            self._porcupine = pvporcupine.create(
                access_key=self.config.access_key,
                keyword_paths=[str(self.config.keyword_path)],
            )

        if self._recorder is None:
            try:
                from pvrecorder import PvRecorder
            except ImportError as exc:
                raise RuntimeError("Porcupine dependencies are not installed.") from exc
            assert self._porcupine is not None
            self._recorder = PvRecorder(device_index=-1, frame_length=self._porcupine.frame_length)
            self._recorder.start()

    def pause(self) -> None:
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder.delete()
            self._recorder = None

    def resume(self) -> None:
        self.start()

    def read(self) -> tuple[list[int], int]:
        if self._porcupine is None or self._recorder is None:
            self.start()
        assert self._porcupine is not None
        assert self._recorder is not None
        pcm = self._recorder.read()
        keyword_index = self._porcupine.process(pcm)
        return pcm, keyword_index

    def close(self) -> None:
        self.pause()
        if self._porcupine is not None:
            self._porcupine.delete()
            self._porcupine = None


@dataclass(slots=True)
class OpenWakeWordEngine:
    config: WakewordConfig
    sample_rate: int = 16000
    frame_length: int = 1280
    _model: Any | None = field(default=None, init=False)
    _stream: Any | None = field(default=None, init=False)
    _resolved_model_key: str | None = field(default=None, init=False)

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            import openwakeword
            from openwakeword.model import Model
            from openwakeword.utils import download_models
        except ImportError as exc:
            raise RuntimeError("openWakeWord dependencies are not installed.") from exc

        inference_framework = "onnx"
        wakeword_models: list[str]
        if self.config.model_path is not None:
            model_path = str(self.config.model_path)
            inference_framework = "onnx" if model_path.endswith(".onnx") else "tflite"
            wakeword_models = [model_path]
        else:
            selected_model = _canonical_openwakeword_name(
                self.config.model_name,
                list(openwakeword.MODELS.keys()),
            )
            download_models(model_names=[selected_model])
            wakeword_models = [selected_model]

        self._model = Model(
            wakeword_models=wakeword_models,
            inference_framework=inference_framework,
        )
        self._resolved_model_key = None

    def _open_stream(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_length,
        )
        self._stream.start()

    def start(self) -> None:
        self._load_model()
        self._open_stream()
        if self._resolved_model_key is None:
            self._resolved_model_key = self._resolve_model_key(
                self._predict_scores(np.zeros(self.frame_length, dtype=np.int16))
            )

    def pause(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def resume(self) -> None:
        self.start()

    def _predict_scores(self, pcm: np.ndarray) -> dict[str, float]:
        assert self._model is not None
        scores = self._model.predict(pcm)
        if not isinstance(scores, dict) or not scores:
            raise RuntimeError("openWakeWord returned no prediction scores.")
        return {str(key): float(value) for key, value in scores.items()}

    def _resolve_model_key(self, scores: dict[str, float]) -> str:
        requested = _normalize_model_name(self.config.model_name)
        if not requested:
            return next(iter(scores))

        for key in scores:
            if _normalize_model_name(Path(key).stem) == requested:
                return key

        available = ", ".join(sorted(Path(key).stem for key in scores))
        raise RuntimeError(
            f"openWakeWord model '{self.config.model_name}' is not available. "
            f"Loaded models: {available}"
        )

    def read(self) -> tuple[list[int], int]:
        if self._model is None or self._stream is None:
            self.start()
        assert self._stream is not None

        chunk, _ = self._stream.read(self.frame_length)
        pcm = np.asarray(chunk[:, 0], dtype=np.int16)
        scores = self._predict_scores(pcm)
        if self._resolved_model_key is None:
            self._resolved_model_key = self._resolve_model_key(scores)

        score = scores.get(self._resolved_model_key, 0.0)
        keyword_index = 0 if score >= self.config.threshold else -1
        return pcm.tolist(), keyword_index

    def close(self) -> None:
        self.pause()
        self._model = None
        self._resolved_model_key = None


def build_wakeword_engine(config: WakewordConfig) -> WakewordEngine:
    provider = config.provider.strip().lower()
    if provider == "porcupine":
        return PorcupineWakewordEngine(config)
    if provider == "openwakeword":
        return OpenWakeWordEngine(config)
    raise RuntimeError(f"Unsupported wakeword provider: {config.provider}")
