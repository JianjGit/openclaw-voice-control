from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")
_DELIVERY_TARGET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
_DELIVERY_MODES = frozenset({"off", "mirror"})
_DELIVERY_CHANNELS = frozenset({"discord", "feishu"})
_DELIVERY_FORBIDDEN_KEYS = frozenset(
    {
        "token",
        "password",
        "secret",
        "app_secret",
        "appsecret",
        "client_secret",
        "bot_token",
    }
)


@dataclass(slots=True)
class AppConfig:
    name: str
    platform: str
    base_dir: Path
    log_dir: Path
    log_level: str


@dataclass(slots=True, frozen=True)
class DeliveryConfig:
    mode: str = "off"
    target: str = ""
    include_user_transcript: bool = False


@dataclass(slots=True, frozen=True)
class DeliveryTargetConfig:
    channel: str
    account_id: str
    to: str


@dataclass(slots=True)
class OpenClawConfig:
    base_url: str
    ws_url: str
    token: str
    agent_id: str
    session_key: str
    home_dir: Path
    timeout_seconds: int = 120
    ws_timeout: int = 30
    # Delivery is intentionally independent of session_key. These references live
    # here so the Gateway client can mirror a completed reply without coupling the
    # service layer to channel-specific credentials or SDKs.
    delivery: DeliveryConfig = field(default_factory=DeliveryConfig)
    delivery_targets: dict[str, DeliveryTargetConfig] = field(default_factory=dict)


@dataclass(slots=True)
class STTConfig:
    host: str = "127.0.0.1"
    port: int = 15900


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
    stt: STTConfig
    asr: ASRConfig

    @property
    def delivery(self) -> DeliveryConfig:
        return self.openclaw.delivery

    @property
    def delivery_targets(self) -> dict[str, DeliveryTargetConfig]:
        return self.openclaw.delivery_targets


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


def _int_env_or_config(env_key: str, configured_value: Any, default: int) -> int:
    env_value = os.getenv(env_key)
    if env_value:
        return int(env_value)
    if configured_value is not None and configured_value != f"${{{env_key}}}":
        return int(configured_value)
    return int(default)


def _delivery_value(env_key: str, configured_value: Any, default: str = "") -> str:
    env_value = os.getenv(env_key)
    if env_value is not None:
        return env_value.strip()
    if configured_value is None:
        return default
    if not isinstance(configured_value, str):
        raise ValueError(f"{env_key} / delivery configuration value must be a string")
    return configured_value.strip()


def _delivery_bool(env_key: str, configured_value: Any, default: bool = False) -> bool:
    env_value = os.getenv(env_key)
    if env_value is not None:
        normalized = env_value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ValueError(f"{env_key} must be true or false")
    if configured_value is None:
        return default
    if isinstance(configured_value, bool):
        return configured_value
    raise ValueError("delivery.include_user_transcript must be a boolean true or false")


def _validate_delivery_scalar(label: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if any(char in normalized for char in ("\r", "\n", "\x00")):
        raise ValueError(f"{label} must be a single safe value")
    return normalized


def _validate_delivery_to(label: str, channel: str, value: str) -> str:
    normalized = _validate_delivery_scalar(label, value)

    if channel == "discord":
        # Match the Gateway Discord parser without guessing whether a bare numeric
        # identifier is a user or channel. Human-readable channel names remain valid.
        if re.fullmatch(r"(?:discord:)?(?:channel|user):\d+", normalized, re.IGNORECASE):
            return normalized
        if re.fullmatch(r"<@!?\d+>", normalized):
            return normalized
        if ":" not in normalized and not normalized.isdigit():
            return normalized
        raise ValueError(
            f"{label} is not a valid Discord target; use channel:<id>, user:<id>, "
            "a Discord mention, or an unambiguous channel name"
        )

    if channel == "feishu":
        # Gateway Feishu accepts optional provider prefixes plus chat/group/channel,
        # user/dm/open_id targets, and raw OpenClaw/User IDs composed of safe ID chars.
        target = re.sub(r"^(?:feishu|lark):", "", normalized, flags=re.IGNORECASE).strip()
        if re.fullmatch(
            r"(?:chat|group|channel|user|dm|open_id):[A-Za-z0-9_-]+",
            target,
            re.IGNORECASE,
        ):
            return normalized
        if re.fullmatch(r"[A-Za-z0-9_-]+", target):
            return normalized
        raise ValueError(
            f"{label} is not a valid Feishu target; use user:<id>, open_id:<id>, "
            "chat:<id>, group:<id>, channel:<id>, dm:<id>, or a raw Feishu ID"
        )

    raise ValueError(f"{label} uses unsupported delivery channel: {channel}")


def _load_delivery_config(
    raw_delivery: Any,
    raw_targets: Any,
) -> tuple[DeliveryConfig, dict[str, DeliveryTargetConfig]]:
    if raw_delivery is None:
        raw_delivery = {}
    if raw_targets is None:
        raw_targets = {}
    if not isinstance(raw_delivery, dict):
        raise ValueError("delivery must be a mapping")
    if not isinstance(raw_targets, dict):
        raise ValueError("delivery_targets must be a mapping")

    raw_mode = raw_delivery.get("mode")
    # PyYAML follows YAML 1.1 booleans, where an unquoted `off` becomes False.
    # Accept that exact representation so the documented `mode: off` works.
    if raw_mode is False:
        raw_mode = "off"
    mode = _delivery_value("OPENCLAW_DELIVERY_MODE", raw_mode, "off").lower()
    if mode not in _DELIVERY_MODES:
        raise ValueError("delivery.mode must be one of: off, mirror")
    selected_target = _delivery_value("OPENCLAW_DELIVERY_TARGET", raw_delivery.get("target"), "")
    include_user_transcript = _delivery_bool(
        "OPENCLAW_DELIVERY_INCLUDE_USER_TRANSCRIPT",
        raw_delivery.get("include_user_transcript"),
        False,
    )

    targets: dict[str, DeliveryTargetConfig] = {}
    for raw_name, raw_target in raw_targets.items():
        name = str(raw_name).strip()
        if not name or not _DELIVERY_TARGET_NAME_PATTERN.fullmatch(name):
            raise ValueError(
                "delivery target names may contain only letters, numbers, and underscores"
            )
        if not isinstance(raw_target, dict):
            raise ValueError(f"delivery_targets.{name} must be a mapping")
        forbidden = _DELIVERY_FORBIDDEN_KEYS.intersection(str(key).lower() for key in raw_target)
        if forbidden:
            raise ValueError(
                f"delivery_targets.{name} must not contain channel credentials; "
                "configure credentials in OpenClaw Gateway"
            )

        env_prefix = f"OPENCLAW_DELIVERY_TARGET_{name.upper()}"
        channel = _delivery_value(f"{env_prefix}_CHANNEL", raw_target.get("channel")).lower()
        if channel not in _DELIVERY_CHANNELS:
            allowed = ", ".join(sorted(_DELIVERY_CHANNELS))
            raise ValueError(
                f"delivery_targets.{name}.channel must be a supported external channel: {allowed}"
            )
        account_id = _validate_delivery_scalar(
            f"delivery_targets.{name}.account_id",
            _delivery_value(f"{env_prefix}_ACCOUNT_ID", raw_target.get("account_id"), "default"),
        )
        to = _validate_delivery_to(
            f"delivery_targets.{name}.to",
            channel,
            _delivery_value(f"{env_prefix}_TO", raw_target.get("to")),
        )
        targets[name] = DeliveryTargetConfig(channel=channel, account_id=account_id, to=to)

    if selected_target and selected_target not in targets:
        raise ValueError(
            f"delivery.target references unknown target '{selected_target}'; "
            "it must name a preconfigured delivery_targets entry"
        )
    if mode == "mirror" and not selected_target:
        raise ValueError("delivery.target is required when delivery.mode=mirror")

    return DeliveryConfig(
        mode=mode,
        target=selected_target,
        include_user_transcript=include_user_transcript,
    ), targets


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
    stt = data.get("stt", {})
    audio = data.get("audio", {})
    wakeword = data.get("wakeword", {})
    tts = data.get("tts", {})
    asr = data.get("asr", {})
    delivery, delivery_targets = _load_delivery_config(
        data.get("delivery", {}),
        data.get("delivery_targets", {}),
    )

    app_cfg = AppConfig(
        name=app.get("name", "openclaw-voice-control"),
        platform=app.get("platform", "windows"),
        base_dir=base_dir,
        log_dir=_resolve_path(base_dir, app.get("log_dir", "logs")),
        log_level=app.get("log_level", "INFO").upper(),
    )

    raw_base_url = _env_or_config(
        "OPENCLAW_BASE_URL",
        openclaw.get("base_url"),
        "http://127.0.0.1:18789",
    )
    base_url_clean = raw_base_url.rstrip("/")
    if base_url_clean.endswith("/v1/chat/completions"):
        base_url_clean = base_url_clean[: -len("/v1/chat/completions")]
    configured_ws_url = _env_or_config("OPENCLAW_WS_URL", openclaw.get("ws_url"), "")
    ws_url = configured_ws_url or (
        base_url_clean.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    )

    return VoiceControlConfig(
        app=app_cfg,
        openclaw=OpenClawConfig(
            base_url=base_url_clean,
            ws_url=ws_url,
            token=os.getenv("OPENCLAW_TOKEN", openclaw.get("token", "")),
            agent_id=_env_or_config("OPENCLAW_AGENT_ID", openclaw.get("agent_id"), "main"),
            session_key=_env_or_config(
                "OPENCLAW_SESSION_KEY",
                openclaw.get("session_key"),
                "agent:main:main",
            ),
            home_dir=_env_or_path(
                "OPENCLAW_HOME",
                base_dir,
                openclaw.get("home_dir"),
                "~/.openclaw",
            ),
            timeout_seconds=_int_env_or_config(
                "OPENCLAW_TIMEOUT_SECONDS",
                openclaw.get("timeout_seconds"),
                120,
            ),
            ws_timeout=_int_env_or_config(
                "OPENCLAW_WS_TIMEOUT",
                openclaw.get("ws_timeout"),
                30,
            ),
            delivery=delivery,
            delivery_targets=delivery_targets,
        ),
        stt=STTConfig(
            host=_env_or_config("STT_HOST", stt.get("host"), "127.0.0.1"),
            port=_int_env_or_config("STT_PORT", stt.get("port"), 15900),
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
            followup_beep_enabled=bool(tts.get("followup_beep_enabled", False)),
            followup_beep_sound=tts.get("followup_beep_sound", ""),
            record_done_beep_enabled=bool(tts.get("record_done_beep_enabled", False)),
            record_done_sound=tts.get("record_done_sound", ""),
            no_speech_beep_enabled=bool(tts.get("no_speech_beep_enabled", False)),
            no_speech_sound=tts.get("no_speech_sound", ""),
            post_reply_delay=float(tts.get("post_reply_delay", 0.0)),
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
