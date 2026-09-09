# Release Checklist

## Repository safety

- [ ] No secrets, tokens, private model assets, logs, virtual environments, or runtime caches are tracked.
- [ ] Any credential that ever entered Git history has been rotated.
- [ ] Redistributable status is known for bundled wakeword/model assets.

## Voice Core architecture

- [ ] `src/openclaw_voice_control` has no PySide6, Overlay state, file-based TTS stop flag, launchd, or macOS runtime dependency.
- [ ] `run_service.bat` contains no machine-specific drive paths.
- [ ] Only supported helper scripts remain under `scripts/`.
- [ ] Public event strings and service API signatures match the API design.
- [ ] `VoiceControlService.close()` remains idempotent.
- [ ] ASR access remains serialized across standalone and STT HTTP callers.
- [ ] Gateway WS/session fallback does not duplicate streamed sentence delivery.

## Automated validation

- [ ] Package installs on Windows Python 3.11.
- [ ] `python -m compileall -q src/openclaw_voice_control scripts tests examples` passes.
- [ ] `python -m pytest -q` passes.
- [ ] GitHub Actions Windows workflow is green when Actions are enabled for the repository.

## Machine-level validation

- [ ] Real microphone is visible.
- [ ] openWakeWord or configured Porcupine route detects the wakeword.
- [ ] SenseVoice transcribes a real recording.
- [ ] Windows SAPI5 speaks repeatedly without COM cross-thread errors.
- [ ] Live OpenClaw Gateway conversation returns a final reply without duplicate speech.
- [ ] Local `/stt` endpoint accepts a real file.
- [ ] `speak_message()` and `stop_speaking()` work from an external consumer process/harness.
- [ ] Shutdown releases speech, wakeword, STT HTTP, and Gateway resources.

## Documentation

- [ ] README and README.zh-CN describe the Windows/headless architecture only.
- [ ] `docs/architecture.md`, module docs, scripts README, examples, and skill docs match code.
- [ ] Removed macOS/Overlay paths are not presented as current capabilities.
- [ ] Features not implemented now (desktop-pet UI, follow-up loop, proactive Gateway push listening) are explicitly described as out of scope.
