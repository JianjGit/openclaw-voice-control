# Standalone Main Loop

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

Business/UI state is expressed only through `VoiceEvent` and Presenter. The standalone loop does not write Overlay JSON or TTS stop-flag files.

Recoverable recording, ASR, or Gateway failures emit an `error` event and return to `idle`. Fatal startup/runtime exceptions are logged by `run()` and re-raised after crash-report handling.

`RuntimeControl.request_shutdown()` terminates the loop. `run()` always invokes the idempotent `close()` in `finally`.
