from __future__ import annotations

import tomllib
from pathlib import Path

from openclaw_voice_control import (
    ConsolePresenter,
    NullPresenter,
    Presenter,
    VoiceControlService,
    VoiceEvent,
    VoiceEventKind,
)
from openclaw_voice_control.config import load_config


def test_be_t09_config_defaults_are_windows_headless(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.delenv("OPENCLAW_HOME", raising=False)
    monkeypatch.delenv("STT_HOST", raising=False)
    monkeypatch.delenv("STT_PORT", raising=False)

    config = load_config(config_path)

    assert config.app.platform == "windows"
    assert not hasattr(config, "overlay")
    assert not hasattr(config.app, "runtime_dir")
    assert config.stt.host == "127.0.0.1"
    assert config.stt.port == 15900
    assert config.openclaw.ws_timeout == 30
    assert config.openclaw.timeout_seconds == 120
    assert not hasattr(config.openclaw, "model")
    assert not hasattr(config.openclaw, "user")
    assert config.tts.followup_beep_sound == ""
    assert config.tts.record_done_sound == ""
    assert config.tts.no_speech_sound == ""


def test_be_t09_environment_overrides_runtime_endpoints(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "openclaw:\n"
        "  base_url: http://127.0.0.1:18789\n"
        "  ws_timeout: 11\n"
        "  timeout_seconds: 44\n"
        "stt:\n"
        "  host: 127.0.0.1\n"
        "  port: 15900\n",
        encoding="utf-8",
    )
    home = tmp_path / "openclaw-home"
    monkeypatch.setenv("OPENCLAW_HOME", str(home))
    monkeypatch.setenv("OPENCLAW_WS_URL", "ws://localhost:9999/custom")
    monkeypatch.setenv("OPENCLAW_WS_TIMEOUT", "13")
    monkeypatch.setenv("OPENCLAW_TIMEOUT_SECONDS", "55")
    monkeypatch.setenv("STT_HOST", "127.0.0.2")
    monkeypatch.setenv("STT_PORT", "16000")

    config = load_config(config_path)

    assert config.openclaw.home_dir == home
    assert config.openclaw.ws_url == "ws://localhost:9999/custom"
    assert config.openclaw.ws_timeout == 13
    assert config.openclaw.timeout_seconds == 55
    assert config.stt.host == "127.0.0.2"
    assert config.stt.port == 16000


def test_be_t09_public_import_surface_is_stable() -> None:
    assert VoiceControlService is not None
    assert VoiceEvent is not None
    assert VoiceEventKind is not None
    assert Presenter is not None
    assert NullPresenter is not None
    assert ConsolePresenter is not None


def test_be_t09_pyproject_matches_actual_headless_dependencies() -> None:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = "\n".join(project["project"]["dependencies"]).lower()
    extras = project["project"]["optional-dependencies"]
    scripts = project["project"]["scripts"]

    assert "pyside6" not in dependencies
    assert "torchcodec" not in dependencies
    assert "websockets" in dependencies
    assert "pywin32" in dependencies
    assert "comtypes" not in dependencies
    assert "edge-tts" not in dependencies
    assert any("pvporcupine" in item.lower() for item in extras["porcupine"])
    assert any("comtypes" in item.lower() for item in extras["tts-cli"])
    assert any("edge-tts" in item.lower() for item in extras["tts-cli"])
    assert "openclaw-overlay" not in scripts


def test_be_t09_default_yaml_has_no_overlay_or_macos_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "config" / "default.yaml").read_text(encoding="utf-8")

    assert "overlay:" not in text
    assert "/System/Library/" not in text
    assert "platform: windows" in text
    assert "port: 15900" in text
