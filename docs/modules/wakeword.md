# Wakeword

`wakeword.py` provides a small `WakewordEngine` protocol and two implementations:

- `OpenWakeWordEngine` (default)
- `PorcupineWakewordEngine` (optional)

The standalone service starts the engine once and keeps the model alive across turns. Per-turn microphone handoff uses `pause()` and `resume()`; `close()` is reserved for final teardown.

For openWakeWord, the model name/path and threshold are configurable. The audio stream uses 16 kHz mono PCM. For Porcupine, a keyword `.ppn` file and Picovoice access key are required.

The wakeword layer only reports detections. Recording, ASR, Gateway conversation, events, and TTS are orchestrated by `VoiceControlService`.

The service applies cooldown/rearm timing around detections and defaults to one independent conversation turn per wake.
