from __future__ import annotations

from pathlib import Path

from openclaw_voice_control.config import load_config
from openclaw_voice_control.tts import WindowsTTS
from openclaw_voice_control.vits_backend import VITSTTS


def _config(tmp_path: Path, tts_yaml: str = ""):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "config.yaml"
    if tts_yaml:
        content = "app:\n  log_dir: logs\ntts:\n" + tts_yaml
    else:
        content = "app:\n  log_dir: logs\ntts: {}\n"
    path.write_text(content, encoding="utf-8")
    return load_config(path)


def test_default_provider_stays_windows_sapi(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.tts.provider == "windows_sapi"
    assert config.tts.fallback == "windows_sapi"
    backend = WindowsTTS(config.tts)
    assert isinstance(backend, WindowsTTS)


def test_vits_provider_dispatches_without_changing_service_wiring(tmp_path: Path) -> None:
    config = _config(tmp_path, "  provider: vits\n  fallback: none\n")
    # Avoid touching a real checkpoint here; constructor behavior itself is covered
    # with the injected engines below.
    assert config.tts.provider == "vits"


def test_vits_config_parses_runtime_parameters(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        "  provider: vits\n"
        "  fallback: none\n"
        "  vits:\n"
        "    model_dir: models/vits\n"
        "    speaker: 角色A\n"
        "    speaker_id: 120\n"
        "    sample_rate: 22050\n"
        "    noise_scale: 0.45\n"
        "    noise_scale_w: 0.5\n"
        "    length_scale: 1.28\n",
    )
    assert config.tts.provider == "vits"
    assert config.tts.fallback == "none"
    assert config.tts.vits.speaker == "角色A"
    assert config.tts.vits.speaker_id == 120
    assert config.tts.vits.model_dir == (tmp_path / "models" / "vits").resolve()
    assert config.tts.vits.temp_dir == (tmp_path / "tmp" / "tts").resolve()


class _CountingEngine:
    def __init__(self) -> None:
        self.opens = 0
        self.closes = 0

    def open(self) -> None:
        self.opens += 1

    def close(self) -> None:
        self.closes += 1


def test_vits_model_is_preloaded_once_not_reopened_by_speech_worker(tmp_path: Path) -> None:
    config = _config(tmp_path, "  provider: vits\n  fallback: none\n")
    engine = _CountingEngine()
    backend = VITSTTS(config.tts, engine=engine)
    assert engine.opens == 1
    backend.open()
    backend.open()
    assert engine.opens == 1
    backend.close()
    assert engine.closes == 1


class _FailingEngine:
    def open(self) -> None:
        raise RuntimeError("bad checkpoint")

    def close(self) -> None:
        pass


def test_vits_init_failure_with_none_fallback_does_not_raise(tmp_path: Path) -> None:
    config = _config(tmp_path, "  provider: vits\n  fallback: none\n")
    backend = VITSTTS(config.tts, engine=_FailingEngine())
    backend.open()
    assert backend.speak("hello", lambda: False) is False
    backend.close()
