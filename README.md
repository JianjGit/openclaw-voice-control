# OpenClaw Voice Control

Headless Windows voice core SDK and standalone voice service for OpenClaw.

OpenClaw Voice Control turns a Windows machine into a reusable voice layer: wakeword or push-to-talk input, microphone recording, SenseVoice/FunASR ASR, OpenClaw Gateway conversation, streaming Windows SAPI5 TTS, and a small event/API surface for desktop pets or other applications.

> 中文说明：[`README.zh-CN.md`](README.zh-CN.md)

## Features

- Wakeword detection with openWakeWord; optional Porcupine support.
- Runtime input-mode switching: `wakeword` / `push_to_talk` without restarting the service.
- Push-to-talk / click-to-talk with `listen_once()`.
- Microphone recording with silence-based end detection.
- Local SenseVoice / FunASR speech recognition.
- Local `POST /stt` HTTP endpoint for file transcription.
- OpenClaw Gateway protocol v4 conversation over WebSocket with session fallback.
- Optional, default-off mirroring of final assistant replies to preconfigured Feishu or Discord targets through OpenClaw Gateway.
- Streaming sentence-by-sentence TTS through Windows SAPI5.
- FIFO speech queue with stop / shutdown control.
- Public Python SDK for voice input, text chat, transcription and active speech.
- Framework-neutral `Presenter` + `VoiceEvent` integration for desktop pets and GUI apps.
- Standalone Windows service mode.

The core intentionally contains no desktop-pet UI, PySide6 overlay, character assets, bubbles or animation logic.

## Requirements

- Windows
- Python 3.11+
- A reachable OpenClaw Gateway that supports **protocol v4**
- Local SenseVoice model files
- A microphone for voice input

## Install

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
copy .env.example .env
```

Edit `.env` and configure at least the OpenClaw token and local ASR model paths. Default runtime settings live in [`config/default.yaml`](config/default.yaml).

Optional routes:

```powershell
pip install -e ".[porcupine]"   # optional Porcupine wakeword provider
pip install -e ".[tts-cli]"     # optional edge-tts/comtypes helpers for tts_cli.py
```

## Quick start

### Start one persistent Voice Core

```powershell
.\run_service.bat
```

or:

```powershell
python -m openclaw_voice_control --config config/default.yaml --env-file .env
```

`run()` loads the core once and keeps the same ASR, Gateway, TTS, STT server and service instance alive.

Default mode is `wakeword`:

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

### Optional mirror delivery

Assistant-reply delivery is separate from conversation routing. `OPENCLAW_SESSION_KEY` continues to select the agent/session/context that answers the voice turn. It does not select an IM recipient.

Mirror delivery is disabled by default, so the existing local display/TTS behavior does not produce any external message:

```yaml
delivery:
  mode: off
  target: ""
```

To allow delivery, declare destinations under `delivery_targets` and select one by name:

```yaml
delivery:
  mode: mirror
  target: feishu_jie

delivery_targets:
  feishu_jie:
    channel: feishu
    account_id: default
    to: user:ou_84727a25ab32163de1ebd612aca75627

  discord_home:
    channel: discord
    account_id: default
    to: channel:123456789012345678
```

Only names already declared in `delivery_targets` can be selected. Voice transcripts and model replies are never interpreted as delivery targets. The current Voice Core allowlist supports `feishu` and `discord`; unsupported channels, unknown targets, empty destinations, and credential fields fail configuration loading before the service starts.

Environment variables can override the mode, selected name, or fields of a target that is already declared in YAML:

```dotenv
OPENCLAW_DELIVERY_MODE=mirror
OPENCLAW_DELIVERY_TARGET=feishu_jie
OPENCLAW_DELIVERY_TARGET_FEISHU_JIE_CHANNEL=feishu
OPENCLAW_DELIVERY_TARGET_FEISHU_JIE_ACCOUNT_ID=default
OPENCLAW_DELIVERY_TARGET_FEISHU_JIE_TO=user:ou_84727a25ab32163de1ebd612aca75627
```

Delivery happens only after the final assistant reply has been collected. Voice Core uses the Gateway protocol v4 `send` RPC with one idempotency key and does not request the model again. A delivery error is best-effort: it is logged without the reply body, destination value, token, or provider secret, while the original reply and local TTS continue normally.

Channel credentials such as Feishu app secrets or Discord bot tokens must remain in the OpenClaw Gateway configuration. Do not put them in `delivery_targets` or on the desktop-pet side. Dashboard/WebChat is an internal conversation surface rather than a generic outbound channel; it sees the shared session history through the normal session, and is deliberately not accepted as a mirror target.

### Switch input mode at runtime

```python
service.set_input_mode("wakeword")
service.set_input_mode("push_to_talk")
```

`wakeword` mode:

```text
wakeword engine listening
```

`push_to_talk` mode:

```text
wakeword engine paused
VoiceControlService.run() still alive
ASR / TTS / Gateway remain loaded
wait for listen_once()
```

The wakeword engine is paused/resumed, not destroyed and rebuilt.

`set_input_mode()` returns:

- `True` — applied immediately;
- `False` — queued until the current recording / ASR / Gateway / TTS activity finishes.

You can inspect state with:

```python
service.get_input_mode()
service.get_pending_input_mode()
```

When a deferred switch finally applies, the Presenter receives an `idle` event whose metadata contains:

```python
{
    "source": "input_mode",
    "input_mode": "push_to_talk",
    "mode_change": "applied",
}
```

See [`docs/input-modes.md`](docs/input-modes.md) for the full switching contract.

### Push-to-talk / click-to-talk

With a persistent service running, switch first:

```python
applied = service.set_input_mode("push_to_talk")
```

Then a desktop-pet button, tray action or hotkey can invoke `listen_once()` from a worker thread:

```python
ok = service.listen_once(
    speak=True,
    metadata={"source": "desktop_pet"},
)
```

Flow:

```text
button / hotkey
  -> listen_once()
  -> recording
  -> SenseVoice / FunASR
  -> recognized
  -> OpenClaw Gateway
  -> streaming TTS when speak=True
  -> reply
  -> idle
```

The wakeword path and `listen_once()` share one microphone ownership lock, so they cannot open competing microphone streams.

`listen_once()` also remains usable as a standalone one-shot SDK call when `run()` is not active.

### Other Python SDK APIs

```python
from openclaw_voice_control import NullPresenter, VoiceControlService
from openclaw_voice_control.config import load_config

service = VoiceControlService(
    load_config("config/default.yaml", ".env"),
    presenter=NullPresenter(),
)

reply = service.ask_text("Hello", speak=True)
text = service.transcribe_file("C:/audio/input.wav")
service.speak_message("Voice Core is ready.")
service.stop_speaking()
service.close()
```

Main public methods:

```python
service.set_input_mode(mode)                          # -> bool
service.get_input_mode()                              # -> str
service.get_pending_input_mode()                      # -> str | None
service.listen_once(speak=True, metadata=None)         # -> bool
service.transcribe_file(path, metadata=None)          # -> str
service.ask_text(text, speak=True, metadata=None)     # -> str
service.speak_message(text, metadata=None, wait=False)
service.stop_speaking()
service.run()                                         # persistent blocking service loop
service.close()                                       # idempotent shutdown
```

For desktop pets and other GUI applications, see [`docs/desktop-pet-integration.md`](docs/desktop-pet-integration.md).

## Architecture at a glance

Main technologies:

- **Wakeword:** openWakeWord, optional Porcupine
- **Audio:** `sounddevice` + NumPy
- **ASR:** FunASR + SenseVoice
- **Conversation:** OpenClaw Gateway protocol v4 over WebSocket + session JSONL fallback
- **TTS:** Windows SAPI5
- **Concurrency:** Python threads, locks, conditions, queues and runtime events
- **Integration:** framework-neutral `VoiceEvent` / `Presenter`

High-level structure:

```text
External App / Desktop Pet
        │ API commands
        │ VoiceEvent / Presenter
        ▼
VoiceControlService (persistent)
  ├─ set_input_mode()
  │    ├─ wakeword      -> wakeword resume
  │    └─ push_to_talk -> wakeword pause -> listen_once()
  ├─ shared microphone input lock
  ├─ Recording
  ├─ FunASR / SenseVoice
  ├─ STT HTTP Server
  ├─ OpenClaw Gateway
  ├─ SpeechController
  └─ Windows SAPI5
```

Input mode only decides how the next microphone turn starts. Both paths reuse the same recording, ASR, Gateway and TTS pipeline.

More details: [`docs/architecture.md`](docs/architecture.md).

## Repository layout

```text
openclaw-voice-control/
├─ config/       # default runtime configuration
├─ docs/         # architecture, module, integration and validation docs
├─ examples/     # external-consumer examples
├─ scripts/      # TTS/STT/audio diagnostic utilities
├─ skills/       # OpenClaw skill documentation
├─ src/          # openclaw_voice_control Python package
├─ tests/        # automated tests
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
├─ input-modes.md
│  └─ runtime wakeword / push-to-talk switching and safety rules
│
├─ desktop-pet-integration.md
│  └─ desktop-pet / bridge API and event integration
│
├─ modules/
│  ├─ cli-and-config.md    # configuration and startup
│  ├─ main-loop.md         # persistent run() and input-mode orchestration
│  ├─ wakeword.md          # wakeword providers and lifecycle
│  ├─ record.md            # microphone recording and silence detection
│  ├─ asr.md               # SenseVoice / FunASR
│  ├─ gateway-ws.md        # OpenClaw Gateway protocol v4, WebSocket and response aggregation
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
   ├─ functional-design.md
   ├─ backend-design.md
   ├─ api-design.md
   └─ backend-dev-plan.md
```

## Presenter events

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

`Presenter.emit()` may be called from the service thread, `listen_once()` worker or speech worker. GUI applications must marshal events onto their UI thread.

A minimal bridge is available at [`examples/external_presenter.py`](examples/external_presenter.py).

## Utilities

- [`scripts/tts_cli.py`](scripts/tts_cli.py) — SAPI5 / optional edge-tts file synthesis.
- [`scripts/stt_endpoint_client.py`](scripts/stt_endpoint_client.py) — local `/stt` client.
- [`scripts/list_audio_devices.py`](scripts/list_audio_devices.py) — list audio devices.
- [`scripts/test_microphone.py`](scripts/test_microphone.py) — microphone diagnostic.

See [`scripts/README.md`](scripts/README.md).

## Tests

```powershell
python -m pytest -q
```

Automated coverage includes events, runtime control, speech queue semantics, ASR serialization, STT HTTP, `listen_once()`, runtime input-mode switching, text conversation, active speech, wakeword orchestration, Gateway protocol v4 handshake/aggregation/deduplication, optional mirror delivery, configuration and external Presenter/API integration.

Hardware-dependent validation is documented in [`docs/same-machine-test.md`](docs/same-machine-test.md).
