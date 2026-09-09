# Fresh Clone Validation

This checklist validates the current Windows/headless Voice Core from a clean clone.

## Expected local-only inputs

A fresh clone does not contain secrets or machine-specific model assets. Supply them locally through `.env` and model directories.

Typical local inputs:

- `OPENCLAW_TOKEN`
- optional Porcupine access key / `.ppn`
- SenseVoice model files when running offline
- VAD model files when running offline
- a reachable local OpenClaw runtime

## Clean setup

```powershell
git clone <repo-url>
cd openclaw-voice-control
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
copy .env.example .env
```

Edit `.env` for the local machine, then run the hardware-free checks:

```powershell
python -m compileall -q src/openclaw_voice_control scripts tests examples
python -m pytest -q
```

These checks must not require a microphone, real SAPI output, model downloads, or a live Gateway.

## Machine-level checks

After the unit suite passes, validate hardware/runtime dependencies separately.

1. `python scripts/list_audio_devices.py`
2. `python scripts/test_microphone.py`
3. start the service with `run_service.bat`
4. confirm ASR model initialization succeeds
5. confirm wakeword detection on the selected microphone
6. confirm one complete wake -> record -> ASR -> Gateway -> SAPI turn
7. call `scripts/stt_endpoint_client.py` against a real audio file
8. verify `speak_message()` / `stop_speaking()` from an external Python harness
9. close the service and confirm audio/Gateway resources are released

## Ready checkpoints

A standalone process is ready only after logs show ASR initialization, wakeword initialization, and entry into the idle listening loop. Startup success alone is weaker than a real spoken interaction.

## Common setup failures

- `.env` is missing or still contains placeholder token values.
- local SenseVoice/VAD paths do not exist and network model retrieval is unavailable.
- microphone device index is wrong or another process owns the device.
- the chosen Windows SAPI voice is not installed.
- `OPENCLAW_BASE_URL` / `OPENCLAW_WS_URL` points to the wrong local Gateway.
- `OPENCLAW_HOME` does not point to the session root used by the running OpenClaw instance.
- STT host/port differs between service and client.

## Scope note

There is no macOS launchd deployment, Overlay process, Qt dependency, host-app launcher, or file-based stop/state runtime in the current architecture. Historical validation notes for those paths are intentionally not retained as current setup instructions.
