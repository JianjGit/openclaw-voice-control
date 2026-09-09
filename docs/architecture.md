# Voice Core Architecture

## Scope

`openclaw-voice-control` is a headless Windows voice core. UI is an external consumer concern. The core never imports Qt, writes Overlay state files, or uses file-based TTS stop flags.

## Public boundary

External applications integrate through:

- `VoiceControlService`
- `VoiceEvent` / `VoiceEventKind`
- `Presenter.emit(event)`
- `NullPresenter` / `ConsolePresenter`

Important public service APIs now include:

```python
service.set_input_mode("wakeword")
service.set_input_mode("push_to_talk")
service.get_input_mode()
service.get_pending_input_mode()
service.listen_once()
service.transcribe_file(...)
service.ask_text(...)
service.speak_message(...)
service.stop_speaking()
service.run()
service.close()
```

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
service.py         public API, input-mode state and voice orchestration
cli.py             command-line entrypoint
```

## Persistent service lifecycle

`run()` is the persistent Voice Core lifecycle:

```text
run()
  -> load ASR once
  -> start STT HTTP once
  -> initialize current input mode
  -> idle
  -> keep service alive until shutdown
```

Changing input mode does not rebuild the service.

The same objects remain alive:

- FunASR / SenseVoice
- OpenClaw Gateway client
- SpeechController / Windows TTS
- STT HTTP server
- VoiceControlService

Only wakeword microphone listening is paused/resumed.

## Runtime input modes

```text
                         set_input_mode()
                  +----------------------------+
                  |                            |
                  v                            v
          +---------------+            +----------------+
          |   wakeword    |            | push_to_talk   |
          | engine active |            | engine paused  |
          +-------+-------+            +--------+-------+
                  |                             |
          wake detected                 listen_once()
                  |                             |
                  +-------------+---------------+
                                v
                    shared recorded-turn path
```

### `wakeword`

The wakeword engine owns the idle microphone path and reads frames until a keyword is detected.

### `push_to_talk`

The wakeword engine is paused, while `run()` continues waiting inside the same process. An external bridge can then invoke `listen_once()` whenever the user presses a microphone button/hotkey.

Full switching semantics: [`input-modes.md`](input-modes.md).

## Wakeword turn

```text
wakeword read
  -> keyword detected
  -> mark turn active
  -> wakeword.pause()
  -> wake acknowledgement
  -> recording
  -> transcribe_file()
  -> recognized
  -> ask_text(..., speak=True)
  -> Gateway streaming
  -> SpeechController / SAPI
  -> reply
  -> idle
  -> apply pending input mode, if any
  -> otherwise wakeword.resume()
```

The wakeword stream remains paused for the whole recorded conversation, including ASR, Gateway and TTS. This avoids reopening the wakeword microphone while the current answer is still running.

The default standalone behavior remains one conversation turn per wake. There is no automatic follow-up loop.

## Push-to-talk turn

```text
external bridge
  -> listen_once()
  -> acquire shared microphone lock
  -> recording
  -> transcribe_file()
  -> recognized
  -> ask_text()
  -> Gateway / optional TTS
  -> reply
  -> idle
  -> release microphone lock
  -> apply pending input mode, if any
```

With `run()` active, `listen_once()` is accepted only when the applied mode is `push_to_talk`.

Without `run()`, `listen_once()` remains usable as a standalone one-shot SDK call.

## Deferred mode switching

The service tracks active voice operations across:

- recording turns;
- ASR calls;
- Gateway conversation turns;
- queued/active speech started through the public service API.

If a mode change arrives while activity is in progress:

```text
set_input_mode(new_mode)
  -> pending_input_mode = new_mode
  -> return False
  -> current activity completes normally
  -> active count reaches zero
  -> apply pending mode
```

The current turn is not interrupted just to change microphone trigger mode.

When the switch finally applies, the core emits an existing `idle` event with:

```python
{
    "source": "input_mode",
    "input_mode": "wakeword" | "push_to_talk",
    "mode_change": "applied",
}
```

## Concurrency and ownership

- `_asr_lock` serializes SenseVoice access, including STT HTTP and recorded turns.
- `_turn_lock` serializes OpenClaw text/Gateway turns.
- `_input_mode_lock` is the single microphone-ownership lock shared by wakeword reads/turns and `listen_once()`.
- `_input_mode_condition` stores applied/pending input mode and lets `run()` sleep while push-to-talk mode is active.
- `_wakeword_io_lock` serializes wakeword `read()` / `pause()` / `resume()` / close operations.
- `SpeechController` owns one FIFO worker and one TTS backend instance.
- SAPI COM initialization, speech calls, and COM teardown occur on the speech worker thread.
- `RuntimeControl` uses `threading.Event` for speech-stop and service-shutdown signals.
- Gateway WS snapshots and session fallback snapshots share one response accumulator to prevent duplicate sentence delivery.

## Microphone safety invariant

The important invariant is:

> wakeword input and `listen_once()` must never have active microphone streams at the same time.

### wakeword -> push_to_talk

```text
wait for current input ownership
  -> input lock
  -> wakeword.pause()
  -> mark push_to_talk applied
  -> release input lock
```

### push_to_talk -> wakeword

```text
wait for listen_once() to release input lock
  -> wakeword.resume()
  -> mark wakeword applied
```

The mode is not reported as applied before the microphone handoff is safe.

## Wakeword lifetime

Wakeword engines expose:

```text
start
pause
resume
read
close
```

`pause()` closes only the recorder/audio stream; it does not destroy the loaded model/engine.

- openWakeWord keeps its loaded model while the `sounddevice.InputStream` is closed.
- Porcupine keeps the Porcupine engine while the recorder is released.
- `resume()` reopens input without rebuilding the whole Voice Core.
- `close()` is reserved for final service teardown.

## Other embedded flows

### Pure STT

```text
external caller -> transcribe_file(path) -> serialized ASR -> text
```

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

These activities also participate in deferred mode switching so a mode change is not applied in the middle of ASR/Gateway/TTS work.

## Gateway response model

`GatewayWebSocket` sends `chat.send`, consumes `event.agent` snapshots, and polls the configured OpenClaw session directory as fallback. Both sources advance a single text accumulator. Only newly completed sentences are synchronously forwarded to the caller callback, preserving ordering and avoiding per-sentence callback threads.

ACK timeout and total response timeout are configuration-driven. Websocket proxy disabling is connection-local; process-wide proxy environment variables are not deleted.

## Lifecycle and shutdown

A desktop-pet bridge should normally:

```text
create VoiceControlService once
  -> start run() once on a worker thread
  -> repeatedly set_input_mode(...)
  -> call listen_once() whenever push-to-talk is selected
  -> close() only when the host exits
```

`VoiceControlService.close()` is idempotent and requests shutdown, closes STT HTTP, stops the speech worker, closes wakeword resources, and closes the Gateway client. `run()` always calls it from `finally`.

Mode switching itself never calls `close()` and never restarts the process.

## Configuration roots

The default target is Windows. Important configurable boundaries include:

- OpenClaw base/WS URL, token, agent/session identifiers, session home, ACK timeout, total timeout
- STT HTTP host/port
- ASR model/VAD paths and device
- wakeword provider/model/threshold/rearm settings
- audio device and silence thresholds
- Windows SAPI voice and optional prompt sounds

## Explicit non-goals

The core does not implement:

- Qt/PySide6 UI
- Overlay state JSON or stop flag files
- macOS launchd/install/deploy paths
- desktop-pet artwork, bubbles, animation, or state mapping
- multi-turn follow-up loop
- proactive OpenClaw push/subscription listening

The refactor design record remains under `docs/PRD/2026-09-09-voice-core-sdk-refactor/`.
