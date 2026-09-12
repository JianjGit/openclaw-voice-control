from __future__ import annotations

import textwrap
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "openclaw_voice_control"
HF_BASE = "https://huggingface.co/spaces/ulysses115/vits-uma-genshin-honkai/resolve/main"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected block not found in {path}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")


def fetch_text(relative: str) -> str:
    url = f"{HF_BASE}/{relative}"
    req = urllib.request.Request(url, headers={"User-Agent": "openclaw-voice-control-vendor/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read().decode("utf-8")


def vendor_vits() -> None:
    vendor = SRC / "vits_backend" / "vendor"
    files = [
        "models.py",
        "modules.py",
        "attentions.py",
        "commons.py",
        "utils.py",
        "mel_processing.py",
        "transforms.py",
        "text/__init__.py",
        "text/cleaners.py",
        "text/symbols.py",
        "text/LICENSE",
    ]
    for relative in files:
        content = fetch_text(relative)
        if relative == "models.py":
            content = content.replace(
                "import commons\nimport modules\nimport attentions\nimport monotonic_align\n",
                "from . import commons\nfrom . import modules\nfrom . import attentions\nfrom . import monotonic_align\n",
            ).replace("from commons import ", "from .commons import ")
        elif relative == "modules.py":
            content = content.replace(
                "import commons\nimport transforms\n",
                "from . import commons\nfrom . import transforms\n",
            )
        elif relative == "attentions.py":
            content = content.replace("import commons\n", "from . import commons\n")
        elif relative == "utils.py":
            content = content.replace("import commons\n", "from . import commons\n")
        elif relative == "text/__init__.py":
            content = content.replace("from text import cleaners\n", "from . import cleaners\n")
            content = content.replace("from text.symbols import symbols\n", "from .symbols import symbols\n")
        write(vendor / relative, content)

    write(vendor / "__init__.py", "\"\"\"Vendored classic VITS inference sources.\"\"\"\n")
    write(
        vendor / "monotonic_align" / "__init__.py",
        textwrap.dedent(
            '''\
            """Pure-Python monotonic alignment fallback.

            The referenced Space ships a platform-specific compiled extension. Inference via
            SynthesizerTrn.infer() does not call maximum_path(), but models.py imports the
            module at import time. Keeping this implementation makes the vendored source
            importable on Python 3.11+ without committing a foreign binary.
            """
            from __future__ import annotations

            import numpy as np
            import torch


            def maximum_path(neg_cent: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
                value = neg_cent.detach().cpu().numpy().astype(np.float32, copy=True)
                mask_np = mask.detach().cpu().numpy().astype(bool, copy=False)
                paths = np.zeros_like(value, dtype=np.float32)
                for batch in range(value.shape[0]):
                    y_len = int(mask_np[batch, :, 0].sum())
                    x_len = int(mask_np[batch, 0, :].sum())
                    if y_len <= 0 or x_len <= 0:
                        continue
                    score = np.full((y_len, x_len), -np.inf, dtype=np.float32)
                    direction = np.zeros((y_len, x_len), dtype=np.int8)
                    score[0, 0] = value[batch, 0, 0]
                    for y in range(1, y_len):
                        x_min = max(0, x_len + y - y_len)
                        x_max = min(x_len - 1, y)
                        for x in range(x_min, x_max + 1):
                            stay = score[y - 1, x]
                            advance = score[y - 1, x - 1] if x > 0 else -np.inf
                            if advance >= stay:
                                score[y, x] = advance + value[batch, y, x]
                                direction[y, x] = 1
                            else:
                                score[y, x] = stay + value[batch, y, x]
                    x = x_len - 1
                    for y in range(y_len - 1, -1, -1):
                        paths[batch, y, x] = 1.0
                        if y > 0 and direction[y, x] == 1:
                            x -= 1
                return torch.from_numpy(paths).to(device=neg_cent.device, dtype=neg_cent.dtype) * mask
            '''
        ),
    )
    write(
        vendor / "NOTICE.md",
        textwrap.dedent(
            '''\
            # Vendored classic VITS inference source

            Source: https://huggingface.co/spaces/ulysses115/vits-uma-genshin-honkai

            The Python inference files in this directory are vendored from the public Space
            requested for model compatibility. Local import statements were changed only so
            the code lives inside `openclaw_voice_control.vits_backend.vendor`.

            The upstream Space declares Apache-2.0. The text frontend also carries its
            upstream LICENSE in `text/LICENSE`.

            Model weights are intentionally not vendored. `config.json` and `G_953000.pth`
            must be supplied locally through runtime configuration.
            '''
        ),
    )


def write_runtime_backend() -> None:
    write(
        SRC / "vits_backend" / "__init__.py",
        "from .backend import ClassicVITSEngine, VITSTTS\n\n__all__ = [\"ClassicVITSEngine\", \"VITSTTS\"]\n",
    )
    write(
        SRC / "vits_backend" / "backend.py",
        textwrap.dedent(
            '''\
            from __future__ import annotations

            import json
            import logging
            import os
            import tempfile
            import time
            from pathlib import Path
            from typing import Any, Callable

            from ..config import TTSConfig, VITSConfig
            from ..tts import WindowsTTS, play_sound_async, play_wav_interruptible


            class _HParams(dict):
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
                    if self._model is not None:
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
                            "VITS dependencies are not installed; run pip install -e \".[vits]\" "
                            "inside the openclaw-voice-control virtual environment"
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

                    checkpoint = torch.load(str(checkpoint_path), map_location=device, weights_only=False)
                    state_dict = checkpoint.get("model", checkpoint) if isinstance(checkpoint, dict) else checkpoint
                    if not isinstance(state_dict, dict):
                        raise RuntimeError("VITS checkpoint does not contain a model state_dict")
                    incompatible = model.load_state_dict(state_dict, strict=False)
                    if incompatible.missing_keys:
                        self.logger.warning(
                            "VITS checkpoint has %d missing keys; first=%s",
                            len(incompatible.missing_keys),
                            incompatible.missing_keys[:3],
                        )
                    if incompatible.unexpected_keys:
                        self.logger.debug(
                            "VITS checkpoint has %d unexpected keys; first=%s",
                            len(incompatible.unexpected_keys),
                            incompatible.unexpected_keys[:3],
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
                        "VITS model loaded once | dir=%s checkpoint=%s speaker=%s speaker_id=%d sample_rate=%d device=%s",
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
                                    "VITS speaker name %r maps to id=%d but configured speaker_id=%d; using explicit id",
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
                    frontend_text = text
                    if "zh_ja_mixture_cleaners" in cleaners:
                        language = self.config.language.strip().lower()
                        if language in {"zh", "zh-cn", "chinese"}:
                            frontend_text = f"[ZH]{text}[ZH]"
                        elif language in {"ja", "jp", "japanese"}:
                            frontend_text = f"[JA]{text}[JA]"
                    sequence, cleaned = self._text_to_sequence(frontend_text, hps.symbols, cleaners)
                    if getattr(hps.data, "add_blank", False):
                        sequence = self._commons.intersperse(sequence, 0)
                    if not sequence:
                        raise ValueError(f"VITS text frontend produced an empty sequence from {text!r}; cleaned={cleaned!r}")
                    return self._torch.LongTensor(sequence)

                def synthesize_to_wav(self, text: str, wav_path: str | Path) -> Path:
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
                """SpeechBackend using one process-lifetime VITS engine with optional SAPI fallback."""

                def __init__(
                    self,
                    config: TTSConfig,
                    temp_dir: Path,
                    *,
                    logger: logging.Logger | None = None,
                    engine: ClassicVITSEngine | None = None,
                ) -> None:
                    self.config = config
                    self.temp_dir = Path(temp_dir)
                    self.logger = logger or logging.getLogger("openclaw.voice_control.vits")
                    self._engine = engine or ClassicVITSEngine(config.vits, logger=self.logger)
                    self._fallback = WindowsTTS(config) if config.fallback == "windows_sapi" else None
                    self._fallback_open = False
                    self._primary_ready = False
                    self._opened = False

                def open(self) -> None:
                    if self._opened:
                        return
                    self._opened = True
                    self.temp_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        self._engine.open()
                        self._primary_ready = True
                    except Exception:
                        self.logger.exception("VITS initialization failed")
                        self._primary_ready = False
                        self._activate_fallback("VITS initialization failure")

                def _activate_fallback(self, reason: str) -> bool:
                    if self._fallback is None:
                        self.logger.error("%s; tts.fallback=none so this utterance will be silent", reason)
                        return False
                    if self._fallback_open:
                        return True
                    try:
                        self._fallback.open()
                        self._fallback_open = True
                        self.logger.warning("%s; falling back to Windows SAPI", reason)
                        return True
                    except Exception:
                        self.logger.exception("Windows SAPI fallback initialization failed after %s", reason)
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
                        return play_wav_interruptible(str(temp_path), should_stop)
                    except Exception:
                        self.logger.exception("VITS inference/playback failed")
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
                    play_sound_async(sound_path)

                def close(self) -> None:
                    try:
                        self._engine.close()
                    finally:
                        if self._fallback_open and self._fallback is not None:
                            self._fallback.close()
                        self._fallback_open = False
                        self._primary_ready = False
                        self._opened = False
            '''
        ),
    )


def patch_config() -> None:
    path = SRC / "config.py"
    replace_once(
        path,
        '''@dataclass(slots=True)\nclass TTSConfig:\n    engine: str\n    voice: str\n    wake_ack: str\n    followup_beep_enabled: bool\n    followup_beep_sound: str\n    record_done_beep_enabled: bool\n    record_done_sound: str\n    no_speech_beep_enabled: bool\n    no_speech_sound: str\n    post_reply_delay: float\n''',
        '''@dataclass(slots=True)\nclass VITSConfig:\n    model_dir: Path | None = None\n    config_file: str = "config.json"\n    checkpoint_file: str = "G_953000.pth"\n    speaker: str = ""\n    speaker_id: int | None = None\n    sample_rate: int = 22050\n    language: str = "zh"\n    device: str = "cpu"\n    noise_scale: float = 0.45\n    noise_scale_w: float = 0.5\n    length_scale: float = 1.28\n\n\n@dataclass(slots=True)\nclass TTSConfig:\n    engine: str\n    provider: str\n    fallback: str\n    voice: str\n    wake_ack: str\n    followup_beep_enabled: bool\n    followup_beep_sound: str\n    record_done_beep_enabled: bool\n    record_done_sound: str\n    no_speech_beep_enabled: bool\n    no_speech_sound: str\n    post_reply_delay: float\n    vits: VITSConfig = field(default_factory=VITSConfig)\n''',
    )
    replace_once(
        path,
        '    tts = data.get("tts", {})\n    asr = data.get("asr", {})\n',
        '''    tts = data.get("tts", {})\n    if not isinstance(tts, dict):\n        raise ValueError("tts must be a mapping")\n    vits = tts.get("vits", {}) or {}\n    if not isinstance(vits, dict):\n        raise ValueError("tts.vits must be a mapping")\n    tts_provider = str(tts.get("provider", "windows_sapi")).strip().lower()\n    tts_fallback = str(tts.get("fallback", "windows_sapi")).strip().lower()\n    if tts_provider not in {"windows_sapi", "vits"}:\n        raise ValueError("tts.provider must be one of: windows_sapi, vits")\n    if tts_fallback not in {"windows_sapi", "none"}:\n        raise ValueError("tts.fallback must be one of: windows_sapi, none")\n    raw_speaker_id = vits.get("speaker_id")\n    if raw_speaker_id in (None, ""):\n        vits_speaker_id = None\n    else:\n        vits_speaker_id = int(raw_speaker_id)\n    asr = data.get("asr", {})\n''',
    )
    replace_once(
        path,
        '''        tts=TTSConfig(\n            engine=tts.get("engine", "windows_sapi5"),\n            voice=tts.get("voice", "Tingting"),\n            wake_ack=tts.get("wake_ack", "我在"),\n            followup_beep_enabled=bool(tts.get("followup_beep_enabled", False)),\n            followup_beep_sound=tts.get("followup_beep_sound", ""),\n            record_done_beep_enabled=bool(tts.get("record_done_beep_enabled", False)),\n            record_done_sound=tts.get("record_done_sound", ""),\n            no_speech_beep_enabled=bool(tts.get("no_speech_beep_enabled", False)),\n            no_speech_sound=tts.get("no_speech_sound", ""),\n            post_reply_delay=float(tts.get("post_reply_delay", 0.0)),\n        ),\n''',
        '''        tts=TTSConfig(\n            engine=tts.get("engine", "windows_sapi5"),\n            provider=tts_provider,\n            fallback=tts_fallback,\n            voice=tts.get("voice", "Tingting"),\n            wake_ack=tts.get("wake_ack", "我在"),\n            followup_beep_enabled=bool(tts.get("followup_beep_enabled", False)),\n            followup_beep_sound=tts.get("followup_beep_sound", ""),\n            record_done_beep_enabled=bool(tts.get("record_done_beep_enabled", False)),\n            record_done_sound=tts.get("record_done_sound", ""),\n            no_speech_beep_enabled=bool(tts.get("no_speech_beep_enabled", False)),\n            no_speech_sound=tts.get("no_speech_sound", ""),\n            post_reply_delay=float(tts.get("post_reply_delay", 0.0)),\n            vits=VITSConfig(\n                model_dir=_env_or_optional_path("VITS_MODEL_DIR", base_dir, vits.get("model_dir")),\n                config_file=str(vits.get("config_file", "config.json")),\n                checkpoint_file=str(vits.get("checkpoint_file", "G_953000.pth")),\n                speaker=_env_or_config("VITS_SPEAKER", vits.get("speaker"), ""),\n                speaker_id=vits_speaker_id,\n                sample_rate=int(vits.get("sample_rate", 22050)),\n                language=str(vits.get("language", "zh")),\n                device=_env_or_config("VITS_DEVICE", vits.get("device"), "cpu"),\n                noise_scale=float(vits.get("noise_scale", 0.45)),\n                noise_scale_w=float(vits.get("noise_scale_w", 0.5)),\n                length_scale=float(vits.get("length_scale", 1.28)),\n            ),\n        ),\n''',
    )


def patch_tts() -> None:
    path = SRC / "tts.py"
    write(
        path,
        textwrap.dedent(
            '''\
            from __future__ import annotations

            import os
            import subprocess
            import threading
            import time
            import wave
            from dataclasses import dataclass, field
            from pathlib import Path
            from typing import Callable

            from .config import TTSConfig


            def _play_sound_once(sound_path: str) -> None:
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
                """Preserve the project's existing fire-and-forget WAV playback flow."""
                if not sound_path or not os.path.exists(sound_path):
                    return
                threading.Thread(target=lambda: _play_sound_once(sound_path), daemon=True).start()


            def play_wav_interruptible(sound_path: str, should_stop: Callable[[], bool]) -> bool:
                """Play a generated WAV through the same Windows sound path and wait for cleanup safety."""
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
                            except Exception:
                                pass
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
                    _play_sound_once(sound_path)


            def build_tts_backend(config: TTSConfig, base_dir: Path, *, logger=None):
                provider = config.provider.strip().lower()
                if provider == "windows_sapi":
                    return WindowsTTS(config)
                if provider == "vits":
                    from .vits_backend import VITSTTS

                    return VITSTTS(config, Path(base_dir) / "tmp" / "tts", logger=logger)
                raise ValueError(f"Unsupported TTS provider: {config.provider}")
            '''
        ),
    )


def patch_speech() -> None:
    path = SRC / "speech.py"
    replace_once(
        path,
        '        self._worker_lock = threading.Lock()\n        self._pending = 0\n',
        '        self._worker_lock = threading.Lock()\n        self._worker_ready = threading.Event()\n        self._worker_init_error: BaseException | None = None\n        self._pending = 0\n',
    )
    replace_once(
        path,
        '''            if self._worker is not None and self._worker.is_alive():\n                return\n            self._worker = threading.Thread(\n''',
        '''            if self._worker is not None and self._worker.is_alive():\n                return\n            self._worker_ready.clear()\n            self._worker_init_error = None\n            self._worker = threading.Thread(\n''',
    )
    replace_once(
        path,
        '    def clear_stop_request(self) -> None:\n        self.runtime.clear_stop_speech()\n',
        '''    def start(self, timeout: float | None = None) -> None:\n        """Start the speech worker and wait until its backend has been opened."""\n        self._ensure_worker()\n        if not self._worker_ready.wait(timeout):\n            raise TimeoutError("speech backend did not finish initialization in time")\n        if self._worker_init_error is not None:\n            raise RuntimeError("speech backend failed to initialize") from self._worker_init_error\n\n    def clear_stop_request(self) -> None:\n        self.runtime.clear_stop_speech()\n''',
    )
    replace_once(
        path,
        '''        try:\n            self.backend.open()\n            while True:\n''',
        '''        try:\n            self.backend.open()\n            self._worker_ready.set()\n            while True:\n''',
    )
    replace_once(
        path,
        '''        except BaseException as exc:\n            self.logger.exception("Speech worker failed to initialize")\n''',
        '''        except BaseException as exc:\n            self._worker_init_error = exc\n            self._worker_ready.set()\n            self.logger.exception("Speech worker failed to initialize")\n''',
    )
    replace_once(
        path,
        '''        finally:\n            try:\n                self.backend.close()\n''',
        '''        finally:\n            self._worker_ready.set()\n            try:\n                self.backend.close()\n''',
    )


def patch_service() -> None:
    path = SRC / "service.py"
    replace_once(path, 'from .tts import WindowsTTS\n', 'from .tts import build_tts_backend\n')
    replace_once(
        path,
        '''        self.client = OpenClawClient(config.openclaw)\n        self.tts = WindowsTTS(config.tts)\n        self.speech = SpeechController(self.tts, self.runtime, self._emit, logger=self.logger)\n''',
        '''        self.client = OpenClawClient(config.openclaw)\n        self.tts = build_tts_backend(config.tts, config.app.base_dir, logger=self.logger)\n        self.speech = SpeechController(self.tts, self.runtime, self._emit, logger=self.logger)\n''',
    )
    replace_once(
        path,
        '''        self.logger.info("ASR model ready")\n        self.stt_server.start()\n\n        if self.get_input_mode() == "wakeword":\n''',
        '''        self.logger.info("ASR model ready")\n        self.stt_server.start()\n\n        if self.config.tts.provider == "vits":\n            self.logger.info("Loading TTS provider=vits once for process lifetime...")\n            self.speech.start()\n            self.logger.info("TTS provider=vits initialized")\n\n        if self.get_input_mode() == "wakeword":\n''',
    )


def patch_dependencies() -> None:
    path = ROOT / "pyproject.toml"
    replace_once(path, '  "numpy>=1.26",\n', '  "numpy>=1.26,<2",\n')
    replace_once(
        path,
        '''tts-cli = [\n  "comtypes>=1.4; sys_platform == 'win32'",\n  "edge-tts>=6.1",\n]\n''',
        '''tts-cli = [\n  "comtypes>=1.4; sys_platform == 'win32'",\n  "edge-tts>=6.1",\n]\nvits = [\n  "scipy>=1.11",\n  "librosa>=0.10,<0.11",\n  "Unidecode>=1.3",\n  "pypinyin>=0.50",\n  "jieba>=0.42",\n  "cn2an>=0.5",\n  "jamo>=0.4",\n  "pyopenjtalk-prebuilt>=0.3.0",\n]\n''',
    )
    requirements = ROOT / "requirements.txt"
    replace_once(requirements, "numpy>=1.26\n", "numpy>=1.26,<2\n")
    append_once(
        requirements,
        "requirements-vits.txt",
        '''# Optional classic VITS dependencies are kept out of the default SAPI install.\n# Install them with: pip install -r requirements-vits.txt''',
    )
    write(
        ROOT / "requirements-vits.txt",
        textwrap.dedent(
            '''\
            -r requirements.txt
            scipy>=1.11
            librosa>=0.10,<0.11
            Unidecode>=1.3
            pypinyin>=0.50
            jieba>=0.42
            cn2an>=0.5
            jamo>=0.4
            pyopenjtalk-prebuilt>=0.3.0
            '''
        ),
    )


def patch_yaml(path: Path) -> None:
    old = '''tts:\n  engine: windows_sapi5\n  voice: zh-CN-HUIHUI\n  wake_ack: 我在\n  followup_beep_enabled: false\n  followup_beep_sound:\n  record_done_beep_enabled: false\n  record_done_sound:\n  no_speech_beep_enabled: false\n  no_speech_sound:\n  post_reply_delay: 0.0\n'''
    new = '''tts:\n  # Default remains Windows SAPI so existing installations do not change behavior.\n  provider: windows_sapi  # windows_sapi | vits\n  fallback: windows_sapi  # windows_sapi | none\n  engine: windows_sapi5  # legacy field retained for compatibility\n  voice: zh-CN-HUIHUI\n  wake_ack: 我在\n  followup_beep_enabled: false\n  followup_beep_sound:\n  record_done_beep_enabled: false\n  record_done_sound:\n  no_speech_beep_enabled: false\n  no_speech_sound:\n  post_reply_delay: 0.0\n  vits:\n    model_dir:\n    config_file: config.json\n    checkpoint_file: G_953000.pth\n    speaker: \"\"\n    speaker_id:\n    sample_rate: 22050\n    language: zh\n    device: cpu\n    noise_scale: 0.45\n    noise_scale_w: 0.5\n    length_scale: 1.28\n'''
    replace_once(path, old, new)


def write_smoke_test() -> None:
    write(
        ROOT / "scripts" / "tts_smoke_test.py",
        textwrap.dedent(
            '''\
            from __future__ import annotations

            import argparse
            import os
            import tempfile
            import wave
            from pathlib import Path

            from openclaw_voice_control.config import load_config
            from openclaw_voice_control.tts import WindowsTTS
            from openclaw_voice_control.vits_backend import ClassicVITSEngine


            def check_vits(config, text: str, keep: bool) -> None:
                temp_dir = config.app.base_dir / "tmp" / "tts"
                temp_dir.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix="vits_smoke_", suffix=".wav", dir=temp_dir)
                os.close(fd)
                path = Path(name)
                engine = ClassicVITSEngine(config.tts.vits)
                try:
                    engine.open()
                    engine.synthesize_to_wav(text, path)
                    with wave.open(str(path), "rb") as wav_file:
                        frames = wav_file.getnframes()
                        rate = wav_file.getframerate()
                    if frames <= 0:
                        raise RuntimeError("VITS smoke test generated an empty WAV")
                    print(f"VITS OK: speaker_id={engine.speaker_id} rate={rate} frames={frames} path={path}")
                    if keep:
                        print("Keeping generated WAV for manual listening.")
                        path = None
                finally:
                    engine.close()
                    if path is not None:
                        path.unlink(missing_ok=True)


            def check_sapi(config, text: str, speak: bool) -> None:
                backend = WindowsTTS(config.tts)
                try:
                    backend.open()
                    print("Windows SAPI OK: backend opened successfully")
                    if speak:
                        backend.speak(text, lambda: False)
                finally:
                    backend.close()


            def main() -> int:
                parser = argparse.ArgumentParser(description="TTS smoke tests without connecting to OpenClaw Gateway")
                parser.add_argument("--config", default=None)
                parser.add_argument("--provider", choices=("vits", "windows_sapi", "all"), default="all")
                parser.add_argument("--text", default="你好，这是 VITS 语音合成测试。")
                parser.add_argument("--keep-wav", action="store_true")
                parser.add_argument("--speak-sapi", action="store_true")
                args = parser.parse_args()
                config = load_config(args.config)
                if args.provider in {"vits", "all"}:
                    check_vits(config, args.text, args.keep_wav)
                if args.provider in {"windows_sapi", "all"}:
                    check_sapi(config, args.text, args.speak_sapi)
                return 0


            if __name__ == "__main__":
                raise SystemExit(main())
            '''
        ),
    )


def write_tests() -> None:
    write(
        ROOT / "tests" / "test_tts_provider.py",
        textwrap.dedent(
            '''\
            from __future__ import annotations

            import threading
            from pathlib import Path

            from openclaw_voice_control.config import load_config
            from openclaw_voice_control.events import VoiceEvent
            from openclaw_voice_control.runtime import RuntimeControl
            from openclaw_voice_control.speech import SpeechController
            from openclaw_voice_control.tts import WindowsTTS, build_tts_backend
            from openclaw_voice_control.vits_backend import VITSTTS


            def _config(tmp_path: Path, tts_yaml: str = ""):
                path = tmp_path / "config.yaml"
                path.write_text("app:\n  log_dir: logs\ntts:\n" + tts_yaml, encoding="utf-8")
                return load_config(path)


            def test_default_provider_stays_windows_sapi(tmp_path: Path) -> None:
                config = _config(tmp_path)
                assert config.tts.provider == "windows_sapi"
                assert config.tts.fallback == "windows_sapi"
                assert isinstance(build_tts_backend(config.tts, config.app.base_dir), WindowsTTS)


            def test_vits_config_parses_runtime_parameters(tmp_path: Path) -> None:
                config = _config(
                    tmp_path,
                    "  provider: vits\n  fallback: none\n  vits:\n    model_dir: models/vits\n    speaker: 角色A\n    speaker_id: 120\n    noise_scale: 0.45\n    noise_scale_w: 0.5\n    length_scale: 1.28\n",
                )
                assert config.tts.provider == "vits"
                assert config.tts.vits.speaker == "角色A"
                assert config.tts.vits.speaker_id == 120
                assert config.tts.vits.model_dir == (tmp_path / "models" / "vits").resolve()


            class _DummyBackend:
                def __init__(self) -> None:
                    self.opens = 0
                    self.closes = 0

                def open(self) -> None:
                    self.opens += 1

                def speak(self, text, should_stop) -> bool:
                    return not should_stop()

                def close(self) -> None:
                    self.closes += 1


            def test_speech_start_opens_backend_once() -> None:
                backend = _DummyBackend()
                controller = SpeechController(backend, RuntimeControl(), lambda event: None)
                controller.start(timeout=1.0)
                controller.start(timeout=1.0)
                assert backend.opens == 1
                controller.close()
                assert backend.closes == 1


            class _FailingEngine:
                def open(self) -> None:
                    raise RuntimeError("bad checkpoint")

                def close(self) -> None:
                    pass


            def test_vits_init_failure_with_none_fallback_does_not_raise(tmp_path: Path) -> None:
                config = _config(tmp_path, "  provider: vits\n  fallback: none\n")
                backend = VITSTTS(config.tts, tmp_path / "tmp", engine=_FailingEngine())
                backend.open()
                assert backend.speak("hello", lambda: False) is False
                backend.close()
            '''
        ),
    )


def patch_docs() -> None:
    append_once(
        ROOT / "config" / "README.md",
        "## Optional classic multi-speaker VITS TTS",
        r'''## Optional classic multi-speaker VITS TTS

The default remains `tts.provider: windows_sapi`. To enable the classic multi-speaker
VITS backend, install the optional dependencies in this project's own virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[vits]"
```

Keep model weights outside the repository. A local configuration can use:

```yaml
tts:
  provider: vits
  fallback: windows_sapi  # or none
  voice: zh-CN-HUIHUI
  vits:
    model_dir: 'E:\models\VITS语音模型'
    config_file: config.json
    checkpoint_file: G_953000.pth
    speaker: '角色A'
    speaker_id: 120
    sample_rate: 22050
    language: zh
    device: cpu
    noise_scale: 0.45
    noise_scale_w: 0.5
    length_scale: 1.28
```

`VITS_MODEL_DIR`, `VITS_SPEAKER`, and `VITS_DEVICE` can override the corresponding
local values. The model is opened once by the long-lived speech worker during service
startup and reused for every utterance. Generated WAV files live only under `tmp/tts/`
and are removed after playback.

Without connecting to Gateway, run:

```powershell
.\.venv\Scripts\python.exe scripts\tts_smoke_test.py --config config\local.yaml --provider vits --keep-wav
.\.venv\Scripts\python.exe scripts\tts_smoke_test.py --config config\local.yaml --provider windows_sapi --speak-sapi
```
''',
    )
    append_once(
        ROOT / "README.md",
        "### TTS providers",
        '''### TTS providers

Windows SAPI remains the default. Classic multi-speaker VITS is available as an optional
backend with startup-time model loading and configurable SAPI/no-fallback behavior. See
`config/README.md` and `scripts/tts_smoke_test.py` for local setup and Gateway-free checks.
''',
    )


def main() -> None:
    vendor_vits()
    write_runtime_backend()
    patch_config()
    patch_tts()
    patch_speech()
    patch_service()
    patch_dependencies()
    patch_yaml(ROOT / "config" / "default.yaml")
    patch_yaml(ROOT / "config" / "default.example.yaml")
    write_smoke_test()
    write_tests()
    patch_docs()
    print("VITS integration files generated and patched successfully")


if __name__ == "__main__":
    main()
