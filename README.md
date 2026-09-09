# OpenClaw Voice Control

Headless Windows voice core SDK and standalone voice service for OpenClaw.

The repository owns voice capabilities only: wakeword detection, recording, ASR, the local STT HTTP endpoint, OpenClaw Gateway conversation, Windows SAPI speech, runtime stop/shutdown control, and a small event/Presenter integration API. It does **not** contain a desktop pet, Qt overlay, character artwork, bubbles, or other UI implementation.

## Platform and runtime

- Windows is the supported runtime target.
- Python 3.11+.
- The default realtime TTS backend is Windows SAPI5.
- FunASR SenseVoice is used for local ASR.
- openWakeWord is the default wakeword provider; Porcupine remains optional.
- OpenClaw conversation uses the Gateway WebSocket with session JSONL fallback.

## Install

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
copy .env.example .env
```

Edit `.env` and at minimum configure the OpenClaw token and any local model paths you use. The default YAML is `config/default.yaml`.

Start the standalone service:

```powershell
.\run_service.bat
```

or:

```powershell
python -m openclaw_voice_control --config config/default.yaml --env-file .env
```

## Public Python API

```python
from openclaw_voice_control import (
    ConsolePresenter,
    NullPresenter,
    Presenter,
    VoiceControlService,
    VoiceEvent,
    VoiceEventKind,
)
from openclaw_voice_control.config import load_config

service = VoiceControlService(load_config(), presenter=NullPresenter())
```

The stable service entry points are:

```python
service.transcribe_file(path, metadata=None)          # -> str
service.ask_text(text, speak=True, metadata=None)     # -> str
service.speak_message(text, metadata=None, wait=False)
service.stop_speaking()
service.run()                                         # blocking standalone loop
service.close()                                       # idempotent shutdown
```

`transcribe_file()` uses the same serialized ASR path as the STT HTTP server. `ask_text(..., speak=True)` streams complete sentences into the speech queue; `speak=False` returns text without TTS. `speak_message()` is independent of OpenClaw, ASR, wakeword, and recording.

## Events and Presenter integration

The event protocol contains these stable values:

```text
listening
recognized
thinking
reply
speaking
idle
error
```

Implement a Presenter to bridge Voice Core into another application:

```python
class MyPresenter:
    def emit(self, event):
        ui_queue.put(event)
```

`Presenter.emit()` may be called from the service thread or the speech worker. A GUI integration must marshal events onto its own UI thread. Presenter exceptions are isolated by the core service.

See `examples/external_presenter.py` for a minimal queue-based integration.

## Typical event flows

Text conversation with speech:

```text
thinking -> speaking (0..N) -> reply -> idle
```

Text conversation without speech:

```text
thinking -> reply -> idle
```

External active speech:

```text
speaking -> idle
```

Recoverable standalone errors emit `error` followed by `idle` at the appropriate orchestration boundary.

## STT HTTP endpoint

The standalone service exposes:

```text
POST http://127.0.0.1:15900/stt
Content-Type: application/json

{"path": "C:/audio/input.wav"}
```

Successful response:

```json
{"text": "recognized text"}
```

Host and port are configurable through `STT_HOST` / `STT_PORT` or YAML. `scripts/stt_endpoint_client.py` uses the same defaults and accepts CLI overrides.

## Configuration

Important environment variables:

```text
OPENCLAW_BASE_URL
OPENCLAW_WS_URL
OPENCLAW_TOKEN
OPENCLAW_AGENT_ID
OPENCLAW_SESSION_KEY
OPENCLAW_HOME
OPENCLAW_TIMEOUT_SECONDS
OPENCLAW_WS_TIMEOUT
STT_HOST
STT_PORT
WAKEWORD_PROVIDER
OPENWAKEWORD_MODEL_NAME
OPENWAKEWORD_MODEL_PATH
OPENWAKEWORD_THRESHOLD
PICOVOICE_ACCESS_KEY
WAKEWORD_FILE
SENSEVOICE_MODEL_PATH
SENSEVOICE_VAD_MODEL_PATH
VOICE_CONTROL_CONFIG
```

See `.env.example` and `config/default.yaml` for the current defaults.

## Utility scripts

The supported helper scripts are:

- `scripts/tts_cli.py` — offline SAPI5 / optional edge-tts file synthesis helper.
- `scripts/stt_endpoint_client.py` — thin client for the local `/stt` endpoint.
- `scripts/list_audio_devices.py` — enumerate audio devices.
- `scripts/test_microphone.py` — microphone capture diagnostic.

Legacy Overlay, launchd, macOS deploy/install/restart scripts, and the old silent TTS stub are intentionally removed.

## Tests and CI

Hardware-free tests live in `tests/` and cover the event protocol, runtime control, speech queue semantics, ASR serialization, STT HTTP, text conversation, external speech, standalone orchestration, Gateway aggregation/deduplication, configuration, repository cleanup, text normalization, and external Presenter/API integration.

`.github/workflows/ci.yml` is configured for `windows-latest` with Python 3.11 and runs package installation, `compileall`, and `pytest`. Real microphone, SenseVoice model execution, openWakeWord, Windows SAPI voice output, and a live OpenClaw Gateway remain explicit machine-level integration checks rather than deterministic unit tests.

## Architecture and design

- Current architecture: `docs/architecture.md`
- Module notes: `docs/modules/`
- Refactor design and implementation record: `docs/PRD/2026-09-09-voice-core-sdk-refactor/`
- External integration example: `examples/external_presenter.py`

Capabilities intentionally outside this repository include desktop-pet UI, character assets, animation/state mapping, follow-up conversation loops, and proactive OpenClaw push listening unless a later design explicitly adds them.
