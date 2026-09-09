# CLI and Configuration

`cli.py` is the standalone entrypoint. It loads YAML plus an optional `.env` file, constructs `VoiceControlService`, and calls the blocking `run()` lifecycle.

Configuration is defined in `config.py` with typed dataclasses for app, OpenClaw, STT, audio, wakeword, TTS, and ASR settings. There is no Overlay/runtime-state configuration.

The default file is `config/default.yaml`. `VOICE_CONTROL_CONFIG` can select another file.

Important environment overrides include:

- `OPENCLAW_BASE_URL`, `OPENCLAW_WS_URL`, `OPENCLAW_TOKEN`
- `OPENCLAW_AGENT_ID`, `OPENCLAW_SESSION_KEY`, `OPENCLAW_HOME`
- `OPENCLAW_TIMEOUT_SECONDS`, `OPENCLAW_WS_TIMEOUT`
- `STT_HOST`, `STT_PORT`
- `WAKEWORD_PROVIDER`, `OPENWAKEWORD_MODEL_NAME`, `OPENWAKEWORD_MODEL_PATH`, `OPENWAKEWORD_THRESHOLD`
- `PICOVOICE_ACCESS_KEY`, `WAKEWORD_FILE`
- `SENSEVOICE_MODEL_PATH`, `SENSEVOICE_VAD_MODEL_PATH`

The platform default is Windows. Default prompt-sound paths are empty rather than macOS system paths.
