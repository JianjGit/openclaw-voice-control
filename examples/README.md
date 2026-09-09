# Examples

## `external_presenter.py`

Minimal external-consumer integration for the headless Voice Core.

It implements a thread-safe queue Presenter, constructs `VoiceControlService`, runs the blocking standalone service on a worker thread, and consumes `VoiceEvent` objects outside the core.

The example intentionally contains no Qt/PySide6 or desktop-pet code. A GUI consumer can replace the `queue.Queue` loop with its framework-specific main-thread bridge while keeping the same Presenter contract.
