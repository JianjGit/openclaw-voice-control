# Examples

## `external_presenter.py`

Minimal external-consumer integration for the headless Voice Core.

It implements a thread-safe queue Presenter, constructs `VoiceControlService`, runs the blocking standalone wakeword service on a worker thread, and consumes `VoiceEvent` objects outside the core.

The example intentionally contains no Qt/PySide6 or desktop-pet code. A GUI consumer can replace the `queue.Queue` loop with its framework-specific main-thread bridge while keeping the same Presenter contract.

## `listen_once.py`

Minimal push-to-talk / click-to-talk example.

It constructs `VoiceControlService` without starting `run()`, calls `listen_once()` once, immediately records from the microphone, runs the recorded speech through ASR -> OpenClaw -> optional TTS, and then closes the service.

Run from the repository root after configuring `.env`:

```powershell
python examples/listen_once.py
```

For a real GUI, invoke `listen_once()` from a worker or thread pool rather than the UI thread.
