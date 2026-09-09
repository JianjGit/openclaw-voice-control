# Same-Machine Integration Test

Use this procedure to validate the Windows Voice Core on a real development machine after the hardware-free test suite passes.

## Goal

Prove that the checked-out repository works with the machine's actual microphone, wakeword backend, local ASR models, Windows SAPI voice, OpenClaw Gateway, and local STT HTTP endpoint without relying on any removed Overlay/macOS runtime.

## Preparation

1. Stop other processes that may own the microphone or the configured STT port.
2. Activate the repository `.venv`.
3. Copy `.env.example` to `.env` and fill real local values.
4. Confirm the SenseVoice/VAD paths or network model access.
5. Confirm the OpenClaw Gateway is running and `OPENCLAW_HOME` matches its session root.

## Hardware checks

```powershell
python scripts/list_audio_devices.py
python scripts/test_microphone.py
```

Select a valid `audio.input_device_index` in `config/default.yaml` or a local config override when necessary.

## Standalone service

```powershell
.\run_service.bat
```

Wait until logs show ASR ready, wakeword ready, and the idle listening loop. Then speak the configured wakeword and complete one real interaction.

Success requires:

- wakeword is detected;
- wake acknowledgement plays;
- recording starts and ends on silence;
- ASR produces the intended text;
- OpenClaw receives the turn;
- the final reply is returned;
- streamed speech is ordered and not duplicated;
- the service returns to idle and can accept another independent wake.

## STT HTTP

With the service running:

```powershell
python scripts/stt_endpoint_client.py --input_path C:\audio\sample.wav
```

If using a non-default endpoint, set `STT_HOST` / `STT_PORT` or pass `--host` / `--port`.

## External API checks

From another Python harness, construct the core with a Presenter and verify:

```python
service.transcribe_file(...)
service.ask_text("hello", speak=False)
service.speak_message("hello", wait=True)
service.stop_speaking()
service.close()
```

`examples/external_presenter.py` demonstrates the event bridge used by an external application.

## Shutdown checks

After `close()` or Ctrl+C:

- the STT port is released;
- the microphone is released;
- no SAPI worker remains active;
- the wakeword stream is closed;
- Gateway websocket resources are closed.

## Failure isolation

Treat these as separate layers:

1. package/import/test failure;
2. microphone visibility/capture failure;
3. wakeword detection failure;
4. ASR model/recognition failure;
5. Gateway/session fallback failure;
6. SAPI voice/output failure.

A process reaching the idle loop does not by itself prove the complete audio/Gateway path.
