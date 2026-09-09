# Tests

The test suite is organized by the backend refactor acceptance IDs `BE-T01` through `BE-T12`.

Coverage includes:

- event protocol and Presenter isolation;
- RuntimeControl stop/shutdown semantics;
- SpeechController FIFO, stop, wait, backend lifecycle, and worker failures;
- serialized ASR access and STT HTTP contract;
- `ask_text()` and `speak_message()` public behavior;
- recorded-turn orchestration and wakeword/recording handoff;
- Gateway WS/session aggregation and deduplication;
- configuration, dependency metadata, and public imports;
- removal of Overlay/macOS runtime paths;
- shared text normalization;
- external Presenter/public API smoke integration and idempotent close.

Run the hardware-free suite with:

```powershell
python -m pytest -q
```

The suite must not require a live microphone, a downloaded SenseVoice model, real SAPI playback, wakeword inference, or a live OpenClaw Gateway. Those are covered by the machine-level procedure in `docs/same-machine-test.md`.
