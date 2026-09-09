# Voice Input Modes and Main Loop

`VoiceControlService` exposes two microphone-input modes that share the same recording, ASR, Gateway and TTS pipeline.

## `run()` — standalone wakeword mode

`VoiceControlService.run()` is the blocking standalone lifecycle. Startup loads ASR, starts the local STT HTTP server, starts the wakeword engine, emits startup `idle`, and enters the wakeword loop.

On wakeword detection the service:

1. clears any previous speech-stop request;
2. speaks the wake acknowledgement through `SpeechController`;
3. pauses the wakeword audio stream without discarding the loaded model;
4. starts one prepared recording stream and hands it to `record_until_silence()`;
5. resumes the wakeword stream after recording;
6. calls `handle_one_turn()`, which composes `transcribe_file()` and `ask_text()`;
7. rearms the wakeword path for the next independent turn.

The current default is one conversation turn per wake. There is no follow-up loop.

## `listen_once()` — push-to-talk mode

`VoiceControlService.listen_once()` runs one complete recorded turn immediately and does not wait for or start wakeword detection.

```python
ok = service.listen_once(
    speak=True,
    metadata={"source": "desktop_pet"},
)
```

Flow:

```text
listen_once()
  -> record_until_silence()
  -> transcribe_file()
  -> recognized
  -> ask_text()
  -> streaming TTS when speak=True
  -> reply
  -> idle
```

The method returns `True` when the recorded turn completes successfully and `False` when no valid speech is recorded or the downstream recorded turn fails. Microphone-open failures emit `error(stage="recording") -> idle` and are re-raised to the caller.

`metadata` is forwarded through the recorded-turn events. The default source is `listen_once`, and callers may override it, for example with `source="desktop_pet"`.

## Input-mode exclusivity

`run()` and `listen_once()` are alternative microphone-input modes. They must not run concurrently, and concurrent `listen_once()` calls are also rejected.

The service uses an input-mode lock and raises:

```python
RuntimeError("voice input is already active")
```

instead of allowing multiple recording/wakeword streams to compete for the microphone.

For a push-to-talk desktop application, create `VoiceControlService` but do not call `run()`; submit `listen_once()` to a background worker whenever the user presses the microphone button or hotkey.

Business/UI state is expressed only through `VoiceEvent` and Presenter. Neither input mode writes Overlay JSON or TTS stop-flag files.

Recoverable recording, ASR, or Gateway failures emit an `error` event and return to `idle` at the appropriate orchestration boundary. Fatal standalone startup/runtime exceptions are logged by `run()` and re-raised after crash-report handling.

`RuntimeControl.request_shutdown()` terminates the standalone loop. `run()` always invokes the idempotent `close()` in `finally`.
