# Voice Core Architecture

## Scope

`openclaw-voice-control` is a headless Windows voice core. UI is an external consumer concern. The core never imports Qt, writes Overlay state files, or uses file-based TTS stop flags.

## Public boundary

External applications integrate through:

- `VoiceControlService`
- `VoiceEvent` / `VoiceEventKind`
- `Presenter.emit(event)`
- `NullPresenter` / `ConsolePresenter`

`VoiceControlService` exposes both a standalone wakeword mode (`run()`) and an explicit one-shot microphone mode (`listen_once()`).

`Presenter.emit()` may run on the service thread, a `listen_once()` worker, or the speech worker. UI consumers must marshal events to their own UI thread.

## Main components

```text
config.py          YAML/.env loading and typed runtime configuration
events.py          stable event protocol
presenter.py       Presenter protocol and default implementations
runtime.py         stop-speech and shutdown signals
speech.py          single long-lived FIFO speech worker
tts.py             Windows SAPI5 backend owned by the speech worker
asr.py             FunASR SenseVoice wrapper
stt_server.py      local POST /stt adapter using service.transcribe_file
wakeword.py        openWakeWord / optional Porcupine engines
gateway_ws.py      OpenClaw Gateway websocket + session fallback aggregation
openclaw_client.py thin Gateway wrapper
text.py            shared markdown/emoji normalization
service.py         public API, push-to-talk and standalone wakeword orchestration
cli.py             command-line entrypoint
```

## Standalone flow

```text
run()
  -> load ASR
  -> start STT HTTP
  -> start wakeword engine
  -> idle
  -> wakeword detected
  -> wake acknowledgement through SpeechController
  -> pause wakeword audio stream
  -> record until silence
  -> transcribe_file()
  -> recognized event
  -> ask_text(..., speak=True)
  -> thinking
  -> Gateway streaming
  -> speaking events / queued SAPI playback
  -> reply
  -> idle
  -> resume wakeword stream
```

The default standalone behavior is one turn per wake. A follow-up conversation loop is not part of the current implementation.

## Embedded flows

### Push-to-talk / one-shot listening

```text
external caller -> listen_once()
                -> record until silence
                -> transcribe_file()
                -> recognized
                -> ask_text()
                -> Gateway / optional TTS
                -> reply -> idle
```

`listen_once()` does not start or wait for wakeword detection. It returns `True` when the recorded turn completes successfully and `False` when no valid speech is recorded or the recorded turn fails. `speak=False` keeps the recording + ASR + OpenClaw path but skips TTS.

The default metadata source is `listen_once`; callers can override it for their integration, for example `source="desktop_pet"`.

### Pure STT

```text
external caller -> transcribe_file(path) -> serialized ASR -> text
```

No wakeword, Gateway, or TTS is required.

### Text conversation

```text
external caller -> ask_text(text, speak=False)
                -> thinking -> Gateway -> reply -> idle
```

With `speak=True`, complete streamed sentences are delivered to `SpeechController` in order.

### Proactive speech

```text
external caller -> speak_message(text)
                -> SpeechController -> speaking -> idle
```

This path does not depend on OpenClaw, ASR, wakeword, or recording.

## Concurrency and ownership

- `VoiceControlService._asr_lock` serializes all SenseVoice access, including STT HTTP and recorded turns.
- `VoiceControlService._turn_lock` serializes text/Gateway conversation turns.
- `VoiceControlService._input_mode_lock` prevents `run()` and `listen_once()` from opening competing microphone-input modes and rejects concurrent `listen_once()` calls.
- `SpeechController` owns one FIFO worker and one TTS backend instance.
- SAPI COM initialization, speech calls, and COM teardown all occur on the speech worker thread.
- `RuntimeControl` uses `threading.Event` for speech-stop and service-shutdown signals.
- Gateway WS snapshots and session fallback snapshots share one response accumulator to prevent duplicate sentence delivery.

If another microphone-input mode is already active, `run()` / `listen_once()` raises `RuntimeError("voice input is already active")` rather than racing microphone streams.

## Wakeword and recording handoff

Wakeword engines expose `pause()` / `resume()` for per-turn microphone handoff and `close()` only for final teardown. This keeps the loaded wakeword model alive across turns. A prepared recording stream is started once before handoff; `record_until_silence()` does not start it a second time.

`listen_once()` bypasses the wakeword handoff entirely and lets `record_until_silence()` create its own input stream. Both paths then reuse the same recorded-turn processing.

## Gateway response model

`GatewayWebSocket` sends `chat.send`, consumes `event.agent` snapshots, and polls the configured OpenClaw session directory as fallback. Both sources advance a single text accumulator. Only newly completed sentences are synchronously forwarded to the caller callback, preserving ordering and avoiding per-sentence callback threads.

ACK timeout and total response timeout are configuration-driven. Websocket proxy disabling is connection-local; process-wide proxy environment variables are not deleted.

## Lifecycle

`VoiceControlService.close()` is idempotent and requests shutdown, closes STT HTTP, stops the speech worker, closes wakeword resources, and closes the Gateway client. `run()` always calls it from `finally`.

In push-to-talk mode, consumers normally create `VoiceControlService`, call `listen_once()` from background workers when needed, and call `close()` when the host application exits. They do not start `run()`.

## Configuration roots

The default target is Windows. Important configurable boundaries include:

- OpenClaw base/WS URL, token, agent/session identifiers, session home, ACK timeout, total timeout
- STT HTTP host/port
- ASR model/VAD paths and device
- wakeword provider/model/threshold/rearm settings
- audio device and silence thresholds
- Windows SAPI voice and optional prompt sounds

## Explicit non-goals

The current core does not implement:

- Qt/PySide6 UI
- Overlay state JSON or stop flag files
- macOS launchd/install/deploy paths
- desktop-pet artwork, bubbles, animation, or state mapping
- multi-turn follow-up loop
- proactive OpenClaw push/subscription listening

The refactor design record remains under `docs/PRD/2026-09-09-voice-core-sdk-refactor/`.
