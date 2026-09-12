from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from ..config import TTSConfig, VITSConfig


class _HParams(dict):
    """Minimal recursive attribute wrapper compatible with the upstream VITS config."""

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def _to_hparams(value: Any) -> Any:
    if isinstance(value, dict):
        return _HParams({key: _to_hparams(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_to_hparams(item) for item in value]
    return value


class ClassicVITSEngine:
    """Load one classic multi-speaker VITS checkpoint and reuse it for inference."""

    def __init__(self, config: VITSConfig, *, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("openclaw.voice_control.vits")
        self._model: Any | None = None
        self._torch: Any | None = None
        self._hps: Any | None = None
        self._text_to_sequence: Any | None = None
        self._commons: Any | None = None
        self._speaker_id: int | None = None
        self._sample_rate: int | None = None
        self._device: Any | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def sample_rate(self) -> int:
        if self._sample_rate is None:
            raise RuntimeError("VITS engine is not loaded")
        return self._sample_rate

    @property
    def speaker_id(self) -> int:
        if self._speaker_id is None:
            raise RuntimeError("VITS engine is not loaded")
        return self._speaker_id

    def open(self) -> None:
        """Load config/checkpoint exactly once for this engine instance."""
        if self.loaded:
            return

        model_dir = self.config.model_dir
        if model_dir is None:
            raise RuntimeError("tts.vits.model_dir is required when tts.provider=vits")
        model_dir = Path(model_dir).expanduser().resolve()
        config_path = model_dir / self.config.config_file
        checkpoint_path = model_dir / self.config.checkpoint_file
        if not config_path.is_file():
            raise FileNotFoundError(f"VITS config not found: {config_path}")
        if not checkpoint_path.is_file():
            raise FileNotFoundError(f"VITS checkpoint not found: {checkpoint_path}")

        try:
            import torch
            from .vendor import commons
            from .vendor.models import SynthesizerTrn
            from .vendor.text import text_to_sequence
        except ImportError as exc:
            raise RuntimeError(
                "VITS dependencies are not installed; install the project optional extra "
                "with: python -m pip install -e \".[vits]\""
            ) from exc

        raw = json.loads(config_path.read_text(encoding="utf-8"))
        hps = _to_hparams(raw)
        device_name = self.config.device.strip().lower() or "cpu"
        if device_name.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError(f"VITS device {self.config.device!r} requested but CUDA is unavailable")
        device = torch.device(device_name)

        model = SynthesizerTrn(
            len(hps.symbols),
            hps.data.filter_length // 2 + 1,
            hps.train.segment_size // hps.data.hop_length,
            n_speakers=hps.data.n_speakers,
            **hps.model,
        ).to(device)
        model.eval()

        # Match the public Space's load_checkpoint behavior: use every checkpoint
        # value whose key exists and preserve the freshly initialized value for a
        # model key missing from the checkpoint.
        checkpoint = torch.load(str(checkpoint_path), map_location=device, weights_only=False)
        if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("model"), dict):
            raise RuntimeError("VITS checkpoint does not contain the expected 'model' state_dict")
        saved_state = checkpoint["model"]
        current_state = model.state_dict()
        merged_state = {key: saved_state.get(key, value) for key, value in current_state.items()}
        missing = [key for key in current_state if key not in saved_state]
        model.load_state_dict(merged_state)
        if missing:
            self.logger.warning(
                "VITS checkpoint is missing %d model keys; first=%s",
                len(missing),
                missing[:3],
            )

        speaker_id = self._resolve_speaker_id(hps)
        model_rate = int(getattr(hps.data, "sampling_rate", self.config.sample_rate))
        if self.config.sample_rate and int(self.config.sample_rate) != model_rate:
            self.logger.warning(
                "Configured VITS sample_rate=%d differs from model sampling_rate=%d; using model rate",
                self.config.sample_rate,
                model_rate,
            )

        self._torch = torch
        self._commons = commons
        self._text_to_sequence = text_to_sequence
        self._hps = hps
        self._device = device
        self._speaker_id = speaker_id
        self._sample_rate = model_rate
        self._model = model
        self.logger.info(
            "VITS model loaded once | dir=%s checkpoint=%s speaker=%s speaker_id=%d "
            "sample_rate=%d device=%s",
            model_dir,
            self.config.checkpoint_file,
            self.config.speaker or "(by id)",
            speaker_id,
            model_rate,
            device,
        )

    def _resolve_speaker_id(self, hps: Any) -> int:
        n_speakers = int(hps.data.n_speakers)
        speakers = list(getattr(hps, "speakers", []) or [])
        configured_id = self.config.speaker_id
        if configured_id is not None:
            speaker_id = int(configured_id)
            if speaker_id < 0 or speaker_id >= n_speakers:
                raise ValueError(
                    f"tts.vits.speaker_id={speaker_id} is outside model range 0..{n_speakers - 1}"
                )
            if self.config.speaker and self.config.speaker in speakers:
                named_id = speakers.index(self.config.speaker)
                if named_id != speaker_id:
                    self.logger.warning(
                        "VITS speaker name %r maps to id=%d but configured speaker_id=%d; "
                        "using explicit id",
                        self.config.speaker,
                        named_id,
                        speaker_id,
                    )
            return speaker_id

        speaker = self.config.speaker.strip()
        if speaker.isdigit():
            speaker_id = int(speaker)
        elif speaker and speaker in speakers:
            speaker_id = speakers.index(speaker)
        else:
            preview = ", ".join(speakers[:8])
            raise ValueError(
                f"VITS speaker {speaker!r} was not found; configure tts.vits.speaker_id explicitly. "
                f"First speakers: {preview}"
            )
        if speaker_id < 0 or speaker_id >= n_speakers:
            raise ValueError(f"VITS speaker id {speaker_id} is outside model range 0..{n_speakers - 1}")
        return speaker_id

    def _prepare_sequence(self, text: str) -> Any:
        if not self.loaded:
            raise RuntimeError("ClassicVITSEngine.open() must be called before inference")
        assert self._hps is not None
        assert self._text_to_sequence is not None
        assert self._commons is not None
        assert self._torch is not None

        hps = self._hps
        cleaners = list(hps.data.text_cleaners)
        # Match the requested Space's app.py preprocessing before adding language tags.
        frontend_text = text.replace("\n", " ").replace("\r", "").replace(" ", "")
        if "zh_ja_mixture_cleaners" in cleaners:
            language = self.config.language.strip().lower()
            if language in {"zh", "zh-cn", "chinese"}:
                frontend_text = f"[ZH]{frontend_text}[ZH]"
            elif language in {"ja", "jp", "japanese"}:
                frontend_text = f"[JA]{frontend_text}[JA]"

        sequence, cleaned = self._text_to_sequence(frontend_text, hps.symbols, cleaners)
        if getattr(hps.data, "add_blank", False):
            sequence = self._commons.intersperse(sequence, 0)
        if not sequence:
            raise ValueError(
                f"VITS text frontend produced an empty sequence from {text!r}; cleaned={cleaned!r}"
            )
        return self._torch.LongTensor(sequence)

    def synthesize_to_wav(self, text: str, wav_path: str | Path) -> Path:
        """Run inference with the already-loaded model and write a PCM16 WAV."""
        if not text.strip():
            raise ValueError("VITS synthesis text must not be empty")
        if not self.loaded:
            raise RuntimeError("ClassicVITSEngine.open() must be called before inference")
        assert self._torch is not None
        assert self._model is not None
        assert self._device is not None

        import numpy as np
        from scipy.io.wavfile import write as write_wav

        x = self._prepare_sequence(text).unsqueeze(0).to(self._device)
        x_lengths = self._torch.LongTensor([x.size(1)]).to(self._device)
        sid = self._torch.LongTensor([self.speaker_id]).to(self._device)
        with self._torch.inference_mode():
            audio = self._model.infer(
                x,
                x_lengths,
                sid=sid,
                noise_scale=float(self.config.noise_scale),
                noise_scale_w=float(self.config.noise_scale_w),
                length_scale=float(self.config.length_scale),
            )[0][0, 0].detach().float().cpu().numpy()

        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
        output = Path(wav_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        write_wav(str(output), self.sample_rate, pcm)
        if not output.is_file() or output.stat().st_size <= 44:
            raise RuntimeError("VITS inference did not produce a valid WAV payload")
        return output

    def close(self) -> None:
        model = self._model
        self._model = None
        self._hps = None
        self._text_to_sequence = None
        self._commons = None
        self._speaker_id = None
        self._sample_rate = None
        self._device = None
        torch = self._torch
        self._torch = None
        del model
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()


class VITSTTS:
    """Speech backend that preloads one VITS model and optionally falls back to SAPI."""

    def __init__(
        self,
        config: TTSConfig,
        *,
        fallback_factory: Callable[[], Any] | None = None,
        logger: logging.Logger | None = None,
        engine: ClassicVITSEngine | None = None,
    ) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("openclaw.voice_control.vits")
        self.temp_dir = Path(config.vits.temp_dir) if config.vits.temp_dir is not None else Path("tmp/tts")
        self._engine = engine or ClassicVITSEngine(config.vits, logger=self.logger)
        self._fallback_factory = fallback_factory
        self._fallback: Any | None = None
        self._fallback_open = False
        self._primary_ready = False
        self._opened = False
        # Deliberately load during VoiceControlService construction. The service is a
        # resident process, so this happens once at process startup and never per utterance.
        self._prepare_primary_once()

    def _prepare_primary_once(self) -> None:
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            self._engine.open()
            self._primary_ready = True
        except Exception:
            self._primary_ready = False
            self.logger.exception("VITS initialization failed; voice-control process will continue")

    def open(self) -> None:
        """Open thread-affine fallback resources; never reload the VITS weights here."""
        if self._opened:
            return
        self._opened = True
        if not self._primary_ready:
            self._activate_fallback("VITS initialization failure")

    def _activate_fallback(self, reason: str) -> bool:
        if self.config.fallback != "windows_sapi":
            self.logger.error("%s; tts.fallback=none so this utterance will be silent", reason)
            return False
        if self._fallback_open:
            return True
        if self._fallback_factory is None:
            self.logger.error("%s; Windows SAPI fallback factory is unavailable", reason)
            return False
        try:
            self._fallback = self._fallback_factory()
            self._fallback.open()
            self._fallback_open = True
            self.logger.warning("%s; falling back to Windows SAPI", reason)
            return True
        except Exception:
            self.logger.exception("Windows SAPI fallback initialization failed after %s", reason)
            self._fallback = None
            self._fallback_open = False
            return False

    def speak(self, text: str, should_stop: Callable[[], bool]) -> bool:
        if not text:
            return True
        if not self._opened:
            raise RuntimeError("VITSTTS.open() must be called in the speech worker before speak()")
        if not self._primary_ready:
            if self._fallback_open and self._fallback is not None:
                return self._fallback.speak(text, should_stop)
            return False

        fd, temp_name = tempfile.mkstemp(prefix="vits_", suffix=".wav", dir=self.temp_dir)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            self._engine.synthesize_to_wav(text, temp_path)
            from ..tts import play_wav_interruptible

            return play_wav_interruptible(str(temp_path), should_stop)
        except Exception:
            self.logger.exception("VITS inference/playback failed; voice-control process will continue")
            self._primary_ready = False
            if self._activate_fallback("VITS inference/playback failure") and self._fallback is not None:
                return self._fallback.speak(text, should_stop)
            return False
        finally:
            self._safe_remove_temp(temp_path)

    def _safe_remove_temp(self, path: Path) -> None:
        try:
            parent = path.resolve().parent
            expected = self.temp_dir.resolve()
        except OSError:
            return
        if parent != expected or not path.name.startswith("vits_") or path.suffix.lower() != ".wav":
            self.logger.error("Refusing to delete non-VITS temp path: %s", path)
            return
        for attempt in range(4):
            try:
                path.unlink(missing_ok=True)
                return
            except PermissionError:
                if attempt == 3:
                    self.logger.warning("Could not remove VITS temp WAV after playback: %s", path)
                    return
                time.sleep(0.05)
            except OSError:
                self.logger.exception("Failed to remove VITS temp WAV: %s", path)
                return

    def play_sound_async(self, sound_path: str) -> None:
        from ..tts import play_sound_async

        play_sound_async(sound_path)

    def close(self) -> None:
        try:
            self._engine.close()
        finally:
            if self._fallback_open and self._fallback is not None:
                self._fallback.close()
            self._fallback = None
            self._fallback_open = False
            self._primary_ready = False
            self._opened = False
