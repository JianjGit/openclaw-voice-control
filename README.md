# OpenClaw Voice Control

Headless Windows voice core SDK and standalone voice service for OpenClaw.

OpenClaw Voice Control turns a Windows machine into a reusable voice layer: it listens for a wakeword, records speech, transcribes with SenseVoice/FunASR, sends text to OpenClaw Gateway, streams the reply into Windows SAPI5 TTS, and exposes a small event/API surface for desktop pets or other external applications.

> 中文说明：[`README.zh-CN.md`](README.zh-CN.md)

## Features

- Wakeword detection with openWakeWord; optional Porcupine support.
- Microphone recording with silence-based end detection.
- Local SenseVoice / FunASR speech recognition.
- Local `POST /stt` HTTP endpoint for file transcription.
- OpenClaw Gateway conversation over WebSocket with session fallback.
- Streaming sentence-by-sentence TTS through Windows SAPI5.
- FIFO speech queue with stop / shutdown control.
- Public Python SDK: text chat, file transcription, active speech, lifecycle control.
- Framework-neutral `Presenter` + `VoiceEvent` integration for desktop pets and GUI apps.
- Standalone Windows service mode.

The core intentionally contains no desktop-pet UI, PySide6 overlay, character assets, bubbles, or animation logic.

## Requirements

- Windows
- Python 3.11+
- A reachable OpenClaw Gateway
- Local SenseVoice model files
- A microphone for standalone voice mode

## Install

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
copy .env.example .env
```

Edit `.env` and configure at least the OpenClaw token and the local ASR model paths you use. Default runtime settings live in [`config/default.yaml`](config/default.yaml).

Optional routes:

```powershell
pip install -e ".[porcupine]"   # optional Porcupine wakeword provider
pip install -e ".[tts-cli]"     # optional edge-tts/comtypes helpers for tts_cli.py
```

## Quick start

### Run as a standalone voice service

```powershell
.\run_service.bat
```

or:

```powershell
python -m openclaw_voice_control --config config/default.yaml --env-file .env
```

The normal standalone flow is:

```text
wakeword
  -> recording
  -> SenseVoice / FunASR
  -> OpenClaw Gateway
  -> streaming reply
  -> SpeechController
  -> Windows SAPI5
  -> idle
```

### Use as a Python SDK

```python
from openclaw_voice_control import NullPresenter, VoiceControlService
from openclaw_voice_control.config import load_config

service = VoiceControlService(
    load_config("config/default.yaml", ".env"),
    presenter=NullPresenter(),
)

reply = service.ask_text("Hello", speak=True)
print(reply)

service.speak_message("Voice Core is ready.")
service.stop_speaking()
service.close()
```

Main public methods:

```python
service.transcribe_file(path, metadata=None)          # -> str
service.ask_text(text, speak=True, metadata=None)     # -> str
service.speak_message(text, metadata=None, wait=False)
service.stop_speaking()
service.run()                                         # blocking standalone loop
service.close()                                       # idempotent shutdown
```

For desktop pets and other GUI applications, see [`docs/desktop-pet-integration.md`](docs/desktop-pet-integration.md).

## Architecture at a glance

Main technologies:

- **Wakeword:** openWakeWord, optional Porcupine
- **Audio:** `sounddevice` + NumPy
- **ASR:** FunASR + SenseVoice
- **Conversation:** OpenClaw Gateway WebSocket + session JSONL fallback
- **TTS:** Windows SAPI5
- **Concurrency:** Python threads, locks, queues and runtime events
- **Integration:** framework-neutral `VoiceEvent` / `Presenter`

High-level structure:

```text
External App / Desktop Pet
        │ API calls
        │ VoiceEvent / Presenter
        ▼
VoiceControlService
  ├─ Wakeword + Recording
  ├─ FunASR / SenseVoice
  ├─ STT HTTP Server
  ├─ OpenClaw Gateway Client
  ├─ SpeechController
  └─ Windows SAPI5
```

The standalone loop and SDK share the same ASR, Gateway and speech components, so GUI consumers do not need a separate voice implementation.

More details: [`docs/architecture.md`](docs/architecture.md).

## Repository layout

```text
openclaw-voice-control/
├─ config/       # default runtime configuration
├─ docs/         # architecture, module notes, integration and validation docs
├─ examples/     # small external-consumer examples
├─ scripts/      # TTS/STT/audio diagnostic utilities
├─ skills/       # OpenClaw skill documentation
├─ src/          # Python package: openclaw_voice_control
├─ tests/        # automated BE-T01..BE-T12 tests
├─ .github/      # GitHub Actions workflow
├─ .env.example  # environment variable template
├─ pyproject.toml
└─ run_service.bat
```

## Documentation map

```text
docs/
├─ architecture.md
│  └─ overall runtime, ownership, threads and lifecycle
│
├─ desktop-pet-integration.md
│  └─ how desktop pets / external GUI apps consume Voice Core APIs and events
│
├─ modules/
│  ├─ cli-and-config.md    # configuration and startup
│  ├─ main-loop.md         # standalone wakeword loop
│  ├─ wakeword.md          # wakeword providers and lifecycle
│  ├─ record.md            # microphone recording and silence detection
│  ├─ asr.md               # SenseVoice / FunASR
│  ├─ gateway-ws.md        # OpenClaw WebSocket and response aggregation
│  ├─ tts.md               # Windows TTS and SpeechController
│  └─ events-and-text.md   # VoiceEvent protocol and text cleanup
│
├─ same-machine-test.md
│  └─ real Windows microphone / ASR / wakeword / SAPI / Gateway validation
│
├─ fresh-clone-validation.md
│  └─ clean-machine installation validation
│
├─ release-checklist.md
│  └─ pre-release checks
│
└─ PRD/2026-09-09-voice-core-sdk-refactor/
   ├─ functional-design.md # expected product behavior
   ├─ backend-design.md    # backend architecture and responsibilities
   ├─ api-design.md        # detailed public API contract
   └─ backend-dev-plan.md  # BE-01..BE-12 implementation record
```

If you need to answer “which API should a desktop pet call?”, start with [`docs/desktop-pet-integration.md`](docs/desktop-pet-integration.md). If you need exact API semantics, continue to the PRD API design.

## Presenter events

External applications can implement a simple Presenter:

```python
class MyPresenter:
    def emit(self, event):
        ui_queue.put(event)
```

Stable event kinds:

```text
listening
recognized
thinking
reply
speaking
idle
error
```

`Presenter.emit()` may be called from the service thread or the speech worker, so GUI applications must marshal events back onto their own UI thread.

A minimal working bridge is available at [`examples/external_presenter.py`](examples/external_presenter.py).

## Utilities

- [`scripts/tts_cli.py`](scripts/tts_cli.py) — SAPI5 / optional edge-tts file synthesis helper.
- [`scripts/stt_endpoint_client.py`](scripts/stt_endpoint_client.py) — client for the local `/stt` endpoint.
- [`scripts/list_audio_devices.py`](scripts/list_audio_devices.py) — list audio devices.
- [`scripts/test_microphone.py`](scripts/test_microphone.py) — microphone capture diagnostic.

See [`scripts/README.md`](scripts/README.md) for details.

## Tests

```powershell
python -m pytest -q
```

The automated suite covers events, runtime control, speech queue semantics, ASR serialization, STT HTTP, text conversation, active speech, standalone orchestration, Gateway aggregation/deduplication, configuration, cleanup regressions and external Presenter/API integration.

Hardware-dependent validation is documented separately in [`docs/same-machine-test.md`](docs/same-machine-test.md).
