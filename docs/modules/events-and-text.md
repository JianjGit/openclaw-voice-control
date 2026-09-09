# Events, Runtime Control, and Text Normalization

## Events

`events.py` defines the stable `VoiceEventKind` values and `VoiceEvent` payload used by external consumers. `presenter.py` defines the `Presenter.emit(event)` protocol plus `NullPresenter` and `ConsolePresenter`.

Core event values are:

```text
listening
recognized
thinking
reply
speaking
idle
error
```

Presenter calls may originate from the service thread or the speech worker. GUI integrations must marshal events to their own UI thread.

## Runtime control

`runtime.py` owns in-memory `threading.Event` signals for speech stop and service shutdown. Stop, clear, and shutdown operations are idempotent and thread-safe. No state/flag files participate in runtime control.

## Text normalization

`text.py` provides `clean_text()` and `clean_text_for_tts()`.

`clean_text()` removes Markdown decoration, links/code formatting noise, emoji, and repeated whitespace while preserving readable text. `clean_text_for_tts()` additionally converts line breaks to spoken sentence boundaries and normalizes terminal punctuation.

Text normalization is shared by speech logic and is independent of any UI or Overlay implementation.
