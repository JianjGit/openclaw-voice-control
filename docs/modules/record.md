# Recording

Recording is implemented by `VoiceControlService.record_until_silence()` using `sounddevice.InputStream` with 16-bit PCM.

The service emits `listening` when recording starts. RMS energy thresholds determine speech start and end; configuration controls sample rate, input device, start timeout, minimum speech duration, end-silence duration, and maximum recording duration.

A short pending-frame buffer keeps the beginning of speech when the start threshold is crossed. Valid audio is written to a temporary WAV file and deleted after the recorded turn is handled.

For wakeword handoff, the caller starts a prepared recording stream once before calling `record_until_silence(prepared_stream=...)`. The recording method detects this case and does not call `start()` again.

No-speech and recording failures are represented as recoverable `error` events followed by `idle`. Optional prompt sounds are handled by `SpeechController` and use Windows-compatible paths when configured.
