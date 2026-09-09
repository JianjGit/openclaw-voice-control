# Same-Machine Integration Test

Use this procedure to validate the Windows Voice Core on a real development machine after the hardware-free test suite passes.

## Goal

Prove that the checked-out repository works with the machine's actual microphone, wakeword backend, local ASR models, Windows SAPI voice, OpenClaw Gateway, local STT HTTP endpoint, and runtime input-mode switching without relying on any removed Overlay/macOS runtime.

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

Wait until logs show ASR ready, wakeword ready, and the service idle state. Then speak the configured wakeword and complete one real interaction.

Success requires:

- wakeword is detected;
- wake acknowledgement plays;
- recording starts and ends on silence;
- ASR produces the intended text;
- OpenClaw receives the turn;
- the final reply is returned;
- streamed speech is ordered and not duplicated;
- the service returns to idle and can accept another independent wake.

## Runtime input-mode switching

Validate this on the **same running `VoiceControlService` instance**. Do not restart the process between steps.

### 1. wakeword -> push_to_talk

From a bridge/test harness connected to the same service instance:

```python
assert service.set_input_mode("push_to_talk") in (True, False)
```

If the call returns `False`, wait for the Presenter event:

```text
idle
source=input_mode
input_mode=push_to_talk
mode_change=applied
```

Then verify:

- wakeword no longer reacts to the configured keyword;
- the process remains alive;
- ASR/Gateway/TTS objects were not rebuilt by the mode switch;
- the microphone is available to `listen_once()`.

### 2. push-to-talk turn

```python
assert service.get_input_mode() == "push_to_talk"
service.listen_once(
    speak=True,
    metadata={"source": "same_machine_test"},
)
```

Verify one full real turn:

```text
button/API trigger
  -> recording
  -> ASR
  -> Gateway
  -> streaming SAPI TTS
  -> idle
```

While this `listen_once()` turn is active, request:

```python
applied = service.set_input_mode("wakeword")
```

Expected:

```text
applied == False
```

and the current recording/ASR/Gateway/TTS must continue normally. The mode should switch only after the turn ends.

### 3. push_to_talk -> wakeword

After the pending switch applies, verify:

```python
service.get_input_mode() == "wakeword"
service.get_pending_input_mode() is None
```

Then say the wakeword again and confirm it detects successfully without re-creating the whole Voice Core process.

### 4. microphone exclusivity

During the test, confirm there is never a state where wakeword listening and `listen_once()` recording are both active.

A practical symptom check:

- no `PortAudioError` / device-busy error when switching modes normally;
- `listen_once()` while the applied mode is still `wakeword` is rejected instead of opening another microphone stream;
- a pending switch completes after the current turn rather than interrupting it.

## STT HTTP

With the service running:

```powershell
python scripts/stt_endpoint_client.py --input_path C:\audio\sample.wav
```

If using a non-default endpoint, set `STT_HOST` / `STT_PORT` or pass `--host` / `--port`.

## External API checks

From another Python harness, construct the core with a Presenter and verify:

```python
service.set_input_mode("push_to_talk")
service.get_input_mode()
service.get_pending_input_mode()
service.listen_once(speak=False)
service.transcribe_file(...)
service.ask_text("hello", speak=False)
service.speak_message("hello", wait=True)
service.stop_speaking()
service.close()
```

Examples:

- `examples/external_presenter.py`
- `examples/runtime_input_modes.py`

## Shutdown checks

After `close()` or Ctrl+C:

- the STT port is released;
- the microphone is released;
- no SAPI worker remains active;
- the wakeword stream/model is closed;
- Gateway websocket resources are closed.

## Failure isolation

Treat these as separate layers:

1. package/import/test failure;
2. microphone visibility/capture failure;
3. wakeword detection or pause/resume failure;
4. runtime input-mode handoff / microphone-lock failure;
5. ASR model/recognition failure;
6. Gateway/session fallback failure;
7. SAPI voice/output failure.

A process reaching idle does not by itself prove the complete audio/Gateway or runtime-switching path.
