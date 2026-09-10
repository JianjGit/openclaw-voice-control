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
- Optional, default-off mirroring of final assistant replies or the full user-transcript/assistant exchange to preconfigured Feishu or Discord targets through OpenClaw Gateway.
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
copy config\default.example.yaml config\local.yaml
```

Edit `.env` and `config/local.yaml` for machine-specific settings. `config/local.yaml` is intentionally ignored by Git, so local endpoint, delivery-target, audio-device and wakeword changes do not block future pulls. The tracked template is [`config/default.example.yaml`](config/default.example.yaml); `run_service.bat` creates `config/local.yaml` from it automatically when the local file is missing. See [`config/README.md`](config/README.md) for migration notes.

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
python -m openclaw_voice_control --config config/local.yaml --env-file .env
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

Mirror delivery is separate from conversation routing. `OPENCLAW_SESSION_KEY` continues to select the agent/session/context that answers the voice turn. It never selects an IM recipient.

Mirror delivery is disabled by default, so the existing local display/TTS behavior does not produce any external message:

```yaml
delivery:
  mode: off
  target: ""
  include_user_transcript: false
```

To allow delivery, declare destinations under `delivery_targets` and select one by name:

```yaml
delivery:
  mode: mirror
  target: feishu_jie
  include_user_transcript: false

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

`include_user_transcript` defaults to `false` for backward compatibility. With the default, only the final assistant reply is mirrored once. When set to `true`, Voice Core first mirrors the recognized user transcript, then performs the normal single `chat.send`, then mirrors the final assistant reply once. It does not submit the transcript through a second `chat.send` and does not request the model twice.

Enabling `include_user_transcript` is privacy-sensitive: recognized speech that would otherwise remain in the OpenClaw conversation/local voice flow is also sent to the selected external target. Only names already declared in `delivery_targets` can be selected. Voice transcripts, model replies and other user input are never interpreted as target names or destinations.

Environment variables can override the mode, selected name, transcript option, or fields of a target that is already declared in YAML:

```dotenv
OPENCLAW_DELIVERY_MODE=mirror
OPENCLAW_DELIVERY_TARGET=feishu_jie
OPENCLAW_DELIVERY_INCLUDE_USER_TRANSCRIPT=true
OPENCLAW_DELIVERY_TARGET_FEISHU_JIE_CHANNEL=feishu
OPENCLAW_DELIVERY_TARGET_FEISHU_JIE_ACCOUNT_ID=default
OPENCLAW_DELIVERY_TARGET_FEISHU_JIE_TO=user:ou_84727a25ab32163de1ebd612aca75627
```

`OPENCLAW_DELIVERY_INCLUDE_USER_TRANSCRIPT` accepts only `true` or `false`. Unsupported channels, unknown targets, empty destinations, invalid booleans and credential fields fail configuration loading before the service starts.

Gateway v4's official `send` request has no message-role, sender-type or arbitrary metadata field. Voice Core therefore does not invent protocol fields. User transcripts use the centralized text format `语音：「…」` so they are distinguishable in Feishu/Discord; assistant replies remain unchanged with no mechanical `assistant:` prefix.

User-transcript and assistant-reply mirror sends receive independent idempotency keys, generated once per logical mirrored message. Streaming sentence callbacks never trigger delivery. Voice Core does not automatically retry mirror sends; any retry in the same logical send path must reuse its existing idempotency key so Gateway-side idempotency can prevent duplicate IM messages.

Both mirror sends are best-effort. A user-transcript delivery failure does not block the normal `chat.send`, reply generation, local display or TTS, and an assistant delivery failure does not change the returned reply or local speech. Delivery logs contain only `kind=user_transcript|assistant_reply`, target name, channel and success status; they do not include reply/transcript text, destination values, tokens or provider error/secret details.

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
    load_config("config/local.yaml", ".env"),
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
├─ config/       # tracked template + gitignored local runtime configuration
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

Automated coverage includes events, runtime control, speech queue semantics, ASR serialization, STT HTTP, `listen_once()`, runtime input-mode switching, text conversation, active speech, wakeword orchestration, Gateway protocol v4 handshake/aggregation/deduplication, optional assistant-only/full-conversation mirror delivery, configuration and external Presenter/API integration.

Hardware-dependent validation is documented in [`docs/same-machine-test.md`](docs/same-machine-test.md).
