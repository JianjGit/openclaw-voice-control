# Wakeword

`wakeword.py` provides a small `WakewordEngine` protocol and two implementations:

- `OpenWakeWordEngine` (default)
- `PorcupineWakewordEngine` (optional)

The persistent Voice Core keeps the wakeword model/engine alive until final service shutdown. Both per-turn microphone handoff and runtime input-mode switching use `pause()` / `resume()`; `close()` is reserved for final teardown.

## Lifecycle

```text
service startup / wakeword mode
  -> start()

wakeword turn starts
  -> pause()
  -> recording / ASR / Gateway / TTS
  -> resume() if the applied input mode is still wakeword

set_input_mode("push_to_talk")
  -> pause()
  -> keep model/engine loaded
  -> wait for listen_once()

set_input_mode("wakeword")
  -> resume()

service.close()
  -> close()
```

For openWakeWord, `pause()` closes the `sounddevice.InputStream` but preserves the loaded model and resolved model key. `resume()` reopens the stream without rebuilding the whole Voice Core.

For Porcupine, `pause()` releases the `PvRecorder` while preserving the Porcupine engine. `resume()` recreates the recorder and continues using the existing engine.

This is why runtime mode switching does not require reloading ASR/TTS/Gateway or restarting the process.

## Configuration

For openWakeWord, the model name/path and threshold are configurable. The audio stream uses 16 kHz mono PCM.

For Porcupine, a keyword `.ppn` file and Picovoice access key are required.

## Responsibilities

The wakeword layer only reports detections and owns its wakeword-specific microphone stream. Recording, ASR, Gateway conversation, input-mode state, events, and TTS are orchestrated by `VoiceControlService`.

Wakeword reads and `listen_once()` share the service-level microphone ownership lock, so wakeword input cannot remain active while push-to-talk recording opens another microphone stream.

The service applies cooldown/rearm timing around detections and defaults to one independent conversation turn per wake.

See also:

- [`../input-modes.md`](../input-modes.md) — runtime `wakeword` / `push_to_talk` switching
- [`main-loop.md`](main-loop.md) — persistent service orchestration
