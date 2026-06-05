from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


@dataclass(slots=True)
class AppConfig:
    name: str
    platform: str
    base_dir: Path
    log_dir: Path
    runtime_dir: Path
    log_level: str


@dataclass(slots=True)
class OpenClawConfig:
    base_url: str
    ws_url: str
    token: str
    agent_id: str
    model: str
    user: str
    session_key: str
    timeout_seconds: int = 120
    ws_timeout: int = 30


@dataclass(slots=True)
class AudioConfig:
    sample_rate: int
    channels: int
    input_device_index: int
    silence_threshold: float
    silence_seconds_end: float
    max_record_seconds: float
    min_speech_seconds: float
    start_timeout_seconds: float
    start_hits_required: int
    start_hit_threshold: float
    max_pending_blocks: int


@dataclass(slots=True)
class WakewordConfig:
    provider: str
    keyword_path: Path | None
    access_key: str
    model_name: str
    model_path: Path | None
    threshold: float
    cooldown_seconds: float
    rearm_seconds_after_turn: float


@dataclass(slots=True)
class TTSConfig:
    engine: str
    voice: str
    wake_ack: str
    followup_beep_enabled: bool
    followup_beep_sound: str
    record_done_beep_enabled: bool
    record_done_sound: str
    no_speech_beep_enabled: bool
    no_speech_sound: str
    post_reply_delay: float


@dataclass(slots=True)
class OverlayConfig:
    enabled: bool
    state_file: Path
    stop_flag_file: Path
    poll_interval_ms: int


@dataclass(slots=True)
class ASRConfig:
    provider: str
    model: str
    vad_model: str
    model_path: Path | None
    vad_model_path: Path | None
    language: str
    device: str
    disable_update: bool
    disable_pbar: bool


@dataclass(slots=True)
class VoiceControlConfig:
    app: AppConfig
    openclaw: OpenClawConfig
    audio: AudioConfig
    wakeword: WakewordConfig
    tts: TTSConfig
    overlay: OverlayConfig
    asr: ASRConfig


def load_env_file(path: str | Path | None) -> None:
    if not path:
        return

    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), match.group(0)), value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _resolve_optional_path(base_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    return _resolve_path(base_dir, value)


def _env_or_config(env_key: str, configured_value: Any, default: str) -> str:
    env_value = os.getenv(env_key)
    if env_value:
        return env_value

    if isinstance(configured_value, str):
        stripped = configured_value.strip()
        if stripped and stripped != f"${{{env_key}}}":
            return stripped

    return default


def _env_or_path(env_key: str, base_dir: Path, configured_value: Any, default: str) -> Path:
    env_value = os.getenv(env_key)
    if env_value:
        return _resolve_path(base_dir, env_value)

    if isinstance(configured_value, str):
        stripped = configured_value.strip()
        if stripped and stripped != f"${{{env_key}}}":
            return _resolve_path(base_dir, stripped)

    return _resolve_path(base_dir, default)


def _env_or_optional_path(env_key: str, base_dir: Path, configured_value: Any) -> Path | None:
    env_value = os.getenv(env_key)
    if env_value:
        return _resolve_path(base_dir, env_value)

    if isinstance(configured_value, str):
        stripped = configured_value.strip()
        if stripped and stripped != f"${{{env_key}}}":
            return _resolve_path(base_dir, stripped)

    return None


def _float_env_or_config(env_key: str, configured_value: Any, default: float) -> float:
    env_value = os.getenv(env_key)
    if env_value:
        return float(env_value)

    if configured_value is not None:
        return float(configured_value)

    return float(default)


def default_config_path() -> Path:
    env_path = os.getenv("VOICE_CONTROL_CONFIG")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "config" / "default.yaml"


def load_config(config_path: str | Path | None = None, env_path: str | Path | None = None) -> VoiceControlConfig:
    load_env_file(env_path)

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else default_config_path()
    raw_data = yaml.safe_load(resolved_config_path.read_text(encoding="utf-8")) or {}
    data = _expand_env(raw_data)
    base_dir = resolved_config_path.resolve().parent.parent

    app = data.get("app", {})
    openclaw = data.get("openclaw", {})
    audio = data.get("audio", {})
    wakeword = data.get("wakeword", {})
    tts = data.get("tts", {})
    overlay = data.get("overlay", {})
    asr = data.get("asr", {})

    app_cfg = AppConfig(
        name=app.get("name", "openclaw-voice-control"),
        platform=app.get("platform", "macos"),
        base_dir=base_dir,
        log_dir=_resolve_path(base_dir, app.get("log_dir", "logs")),
        runtime_dir=_resolve_path(base_dir, app.get("runtime_dir", "runtime")),
        log_level=app.get("log_level", "INFO").upper(),
    )

    # Resolve base URL (strip old /v1/chat/completions suffix if present)
    raw_base_url = _env_or_config(
        "OPENCLAW_BASE_URL",
        openclaw.get("base_url"),
        "http://127.0.0.1:18789",
    )
    base_url_clean = raw_base_url.rstrip("/")
    if base_url_clean.endswith("/v1/chat/completions"):
        base_url_clean = base_url_clean[: -len("/v1/chat/completions")]
    ws_url = base_url_clean.replace("http://", "ws://").replace("https://", "wss://") + "/ws"

    return VoiceControlConfig(
        app=app_cfg,
        openclaw=OpenClawConfig(
            base_url=base_url_clean,
            ws_url=ws_url,
            token=os.getenv("OPENCLAW_TOKEN", openclaw.get("token", "")),
            agent_id=_env_or_config("OPENCLAW_AGENT_ID", openclaw.get("agent_id"), "main"),
            model=_env_or_config("OPENCLAW_MODEL", openclaw.get("model"), "openclaw:main"),
            user=_env_or_config("OPENCLAW_USER", openclaw.get("user"), "openclaw-voice-control"),
            session_key=_env_or_config("OPENCLAW_SESSION_KEY", openclaw.get("session_key"), "agent:main:main"),
            timeout_seconds=int(openclaw.get("timeout_seconds", 120)),
            ws_timeout=int(openclaw.get("ws_timeout", 30)),
        ),
        audio=AudioConfig(
            sample_rate=int(audio.get("sample_rate", 16000)),
            channels=int(audio.get("channels", 1)),
            input_device_index=int(audio.get("input_device_index", -1)),
            silence_threshold=float(audio.get("silence_threshold", 0.008)),
            silence_seconds_end=float(audio.get("silence_seconds_end", 1.2)),
            max_record_seconds=float(audio.get("max_record_seconds", 60)),
            min_speech_seconds=float(audio.get("min_speech_seconds", 0.4)),
            start_timeout_seconds=float(audio.get("start_timeout_seconds", 3.0)),
            start_hits_required=int(audio.get("start_hits_required", 3)),
            start_hit_threshold=float(audio.get("start_hit_threshold", 0.008)),
            max_pending_blocks=int(audio.get("max_pending_blocks", 10)),
        ),
        wakeword=WakewordConfig(
            provider=_env_or_config("WAKEWORD_PROVIDER", wakeword.get("provider"), "openwakeword"),
            keyword_path=_env_or_optional_path(
                "WAKEWORD_FILE",
                base_dir,
                wakeword.get("keyword_path"),
            ),
            access_key=os.getenv("PICOVOICE_ACCESS_KEY", wakeword.get("access_key", "")),
            model_name=_env_or_config(
                "OPENWAKEWORD_MODEL_NAME",
                wakeword.get("model_name"),
                "hey jarvis",
            ),
            model_path=_env_or_optional_path(
                "OPENWAKEWORD_MODEL_PATH",
                base_dir,
                wakeword.get("model_path"),
            ),
            threshold=_float_env_or_config(
                "OPENWAKEWORD_THRESHOLD",
                wakeword.get("threshold"),
                0.65,
            ),
            cooldown_seconds=float(wakeword.get("cooldown_seconds", 1.5)),
            rearm_seconds_after_turn=_float_env_or_config(
                "WAKEWORD_REARM_SECONDS_AFTER_TURN",
                wakeword.get("rearm_seconds_after_turn"),
                2.0,
            ),
        ),
        tts=TTSConfig(
            engine=tts.get("engine", "windows_sapi5"),
            voice=tts.get("voice", "Tingting"),
            wake_ack=tts.get("wake_ack", "我在"),
            followup_beep_enabled=bool(tts.get("followup_beep_enabled", True)),
            followup_beep_sound=tts.get("followup_beep_sound", "/System/Library/Sounds/Glass.aiff"),
            record_done_beep_enabled=bool(tts.get("record_done_beep_enabled", True)),
            record_done_sound=tts.get("record_done_sound", "/System/Library/Sounds/Funk.aiff"),
            no_speech_beep_enabled=bool(tts.get("no_speech_beep_enabled", True)),
            no_speech_sound=tts.get("no_speech_sound", "/System/Library/Sounds/Pop.aiff"),
            post_reply_delay=float(tts.get("post_reply_delay", 0.05)),
        ),
        overlay=OverlayConfig(
            enabled=bool(overlay.get("enabled", True)),
            state_file=_resolve_path(base_dir, overlay.get("state_file", "runtime/overlay_state.json")),
            stop_flag_file=_resolve_path(base_dir, overlay.get("stop_flag_file", "runtime/stop_tts.flag")),
            poll_interval_ms=int(overlay.get("poll_interval_ms", 150)),
        ),
        asr=ASRConfig(
            provider=asr.get("provider", "funasr"),
            model=asr.get("model", "iic/SenseVoiceSmall"),
            vad_model=asr.get("vad_model", "fsmn-vad"),
            model_path=_env_or_optional_path("SENSEVOICE_MODEL_PATH", base_dir, asr.get("model_path")),
            vad_model_path=_env_or_optional_path("SENSEVOICE_VAD_MODEL_PATH", base_dir, asr.get("vad_model_path")),
            language=asr.get("language", "zh"),
            device=asr.get("device", "cpu"),
            disable_update=bool(asr.get("disable_update", True)),
            disable_pbar=bool(asr.get("disable_pbar", True)),
        ),
    )
