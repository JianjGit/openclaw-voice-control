# OpenClaw Voice Control Skill

Use this repository as a Windows, headless voice capability layer for OpenClaw. Do not assume any built-in desktop-pet or Overlay UI.

## Supported capabilities

- Wakeword-triggered one-turn voice conversation.
- Local microphone recording with silence detection.
- FunASR SenseVoice transcription.
- Local `POST /stt` endpoint.
- OpenClaw Gateway WebSocket conversation with session-file fallback.
- Ordered Windows SAPI5 speech output.
- Direct text conversation through `VoiceControlService.ask_text()`.
- External proactive speech through `VoiceControlService.speak_message()`.
- Thread-safe speech stop through `VoiceControlService.stop_speaking()`.
- External application integration through `Presenter.emit(VoiceEvent)`.

## Public integration surface

```python
from openclaw_voice_control import (
    VoiceControlService,
    VoiceEvent,
    VoiceEventKind,
    Presenter,
    NullPresenter,
    ConsolePresenter,
)
from openclaw_voice_control.config import load_config
```

Key methods:

```python
service.transcribe_file(path, metadata=None)
service.ask_text(text, speak=True, metadata=None)
service.speak_message(text, metadata=None, wait=False)
service.stop_speaking()
service.run()
service.close()
```

## Event protocol

Stable event values:

```text
listening
recognized
thinking
reply
speaking
idle
error
```

`Presenter.emit()` may be called from the service thread or the speech worker. UI consumers must forward events to their own UI/main thread.

## Standalone flow

```text
wakeword -> wake acknowledgement -> recording -> transcription
-> recognized -> thinking -> Gateway streaming -> speaking* -> reply -> idle
```

The current implementation performs one conversation turn per wake. Do not describe a follow-up loop as current behavior.

## Embedded use

Use `transcribe_file()` for pure STT without wakeword or Gateway. Use `ask_text(..., speak=False)` for text-only OpenClaw conversation. Use `speak_message()` for proactive text-to-speech without OpenClaw or ASR.

## Local STT endpoint

Default:

```text
POST http://127.0.0.1:15900/stt
{"path": "C:/audio/input.wav"}
```

`STT_HOST` and `STT_PORT` override the endpoint.

## Configuration

Start from `.env.example` and `config/default.yaml`. Important settings include OpenClaw URL/token/session identifiers, `OPENCLAW_HOME`, Gateway timeouts, STT host/port, wakeword model settings, ASR model paths, audio thresholds, and Windows SAPI voice.

## Utility scripts

Supported scripts:

- `scripts/tts_cli.py`
- `scripts/stt_endpoint_client.py`
- `scripts/list_audio_devices.py`
- `scripts/test_microphone.py`

## Boundaries

Do not add or rely on PySide6, Overlay state files, file-based TTS stop flags, macOS launchd/install scripts, desktop-pet artwork, bubbles, animations, proactive Gateway push listening, or multi-turn follow-up behavior unless a later design explicitly introduces them.

For architecture details see `docs/architecture.md`. For the refactor contract and implementation record see `docs/PRD/2026-09-09-voice-core-sdk-refactor/`.
