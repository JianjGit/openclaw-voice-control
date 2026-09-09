# Examples

## `external_presenter.py`

Minimal external-consumer integration for the headless Voice Core.

It implements a thread-safe queue Presenter, constructs `VoiceControlService`, runs the persistent service on a worker thread, and consumes `VoiceEvent` objects outside the core.

The example intentionally contains no Qt/PySide6 or desktop-pet code. A GUI consumer can replace the `queue.Queue` loop with its framework-specific main-thread bridge while keeping the same Presenter contract.

## `runtime_input_modes.py`

Recommended desktop-pet / bridge pattern for runtime mode switching.

It starts one persistent `VoiceControlService.run()` thread, switches the same instance to `push_to_talk`, calls `listen_once()`, then switches back to `wakeword` without restarting the process or rebuilding ASR/TTS/Gateway.

```powershell
python examples/runtime_input_modes.py
```

For a real bridge, treat `set_mode()` and `listen_once()` as commands from the desktop pet and forward Presenter events back to the UI.

## `listen_once.py`

Minimal standalone one-shot push-to-talk example.

It constructs `VoiceControlService` without starting `run()`, calls `listen_once()` once, immediately records from the microphone, runs the recorded speech through ASR -> OpenClaw -> optional TTS, and then closes the service.

```powershell
python examples/listen_once.py
```

This one-shot form remains supported. For a long-lived desktop pet that switches between wakeword and push-to-talk at runtime, prefer `runtime_input_modes.py`.

All blocking voice calls should run on a worker or thread pool rather than the GUI thread.
