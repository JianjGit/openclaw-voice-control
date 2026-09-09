# Scripts

This directory contains the supported helper tools for the Windows Voice Core.

## `tts_cli.py`

File-oriented TTS helper. Supports Windows SAPI5 and optional edge-tts. This is separate from the realtime `SpeechController` queue used by the service.

Examples:

```powershell
python scripts/tts_cli.py --text "你好" --output out.wav --backend sapi5 --voice huihui
python scripts/tts_cli.py --text "你好" --output out.mp3 --backend edge_tts
```

## `stt_endpoint_client.py`

Thin client for the local service `POST /stt` endpoint.

```powershell
python scripts/stt_endpoint_client.py --input_path C:\audio\sample.wav
```

It reads `STT_HOST` / `STT_PORT` and also accepts `--host`, `--port`, and `--timeout` overrides.

## `list_audio_devices.py`

Lists audio devices visible through `sounddevice`/PortAudio. Use it to select `audio.input_device_index`.

## `test_microphone.py`

Simple microphone diagnostic used for machine-level validation before debugging wakeword or ASR behavior.

## Removed legacy scripts

macOS launchd/install/deploy/restart helpers, Overlay launchers, host-app wrappers, and the old silent `tts_simple.py` stub were removed as part of the Windows Voice Core refactor. They are not supported entrypoints.
