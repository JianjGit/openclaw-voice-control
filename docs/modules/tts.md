# Speech and Windows TTS

Realtime speech is split into two layers.

`SpeechController` owns one long-lived FIFO worker, queue state, stop/wait semantics, event emission, and optional completion callbacks. `WindowsTTS` is the Windows SAPI5 backend and owns no queue or UI state.

SAPI COM initialization, `SAPI.SpVoice` creation, speech calls, and COM teardown all happen on the same speech worker thread. This avoids crossing COM apartment/thread boundaries.

Each queued utterance emits `speaking` with the utterance text and metadata when the worker begins it. `stop_speaking()` requests a runtime stop, interrupts the active SAPI utterance, drains pending items, and returns the service to `idle` without using files.

`VoiceControlService.speak_message()` exposes the same queue to external callers and can wait for the specific queued item. OpenClaw streaming sentences use the same controller, so proactive speech and conversation speech share ordering and interruption semantics.

`WindowsTTS.play_sound_async()` is retained for optional wake/recording prompt sounds. Empty or missing sound paths are ignored.

`scripts/tts_cli.py` is a separate file-synthesis helper. It supports SAPI5 and optional edge-tts and is not the realtime service queue implementation.
