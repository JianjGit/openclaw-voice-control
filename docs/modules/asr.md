# ASR and STT

`src/openclaw_voice_control/asr.py` wraps FunASR SenseVoice. Model loading is owned by the service startup path; recognition is exposed to callers through `VoiceControlService.transcribe_file()`.

All service-level ASR access shares one `_asr_lock`. Recorded wakeword turns, embedded Python callers, and the local STT HTTP server therefore cannot enter the same SenseVoice model concurrently.

`transcribe_file(path)` validates that the file exists and preserves ASR exceptions rather than converting failures into empty text.

The HTTP adapter lives in `stt_server.py` and keeps the compatible contract:

```text
POST /stt
{"path": "C:/audio/input.wav"}
```

Default endpoint: `127.0.0.1:15900`; host/port are configurable through YAML or `STT_HOST` / `STT_PORT`.

The STT endpoint does not require wakeword, Gateway conversation, or TTS.
