# OpenClaw Voice Control Skill

把本仓库作为 OpenClaw 的 Windows、无界面语音能力层使用。不要假设核心内置桌宠或 Overlay UI。

## 已支持能力

- 唤醒词触发的一轮语音对话。
- 麦克风录音与静音检测。
- FunASR SenseVoice 本地识别。
- 本地 `POST /stt` 接口。
- OpenClaw Gateway WebSocket + session 文件 fallback。
- Windows SAPI5 有序朗读。
- `VoiceControlService.ask_text()` 直接文字对话。
- `VoiceControlService.speak_message()` 外部主动朗读。
- `VoiceControlService.stop_speaking()` 线程安全停止朗读。
- 通过 `Presenter.emit(VoiceEvent)` 接入外部应用。

## 公开接入面

```python
from openclaw_voice_control import (
    VoiceControlService,
    VoiceEvent,
    VoiceEventKind,
    Presenter,
    NullPresenter,
    ConsolePresenter,
)
from openclaw_voice_control.config import load_config
```

关键方法：

```python
service.transcribe_file(path, metadata=None)
service.ask_text(text, speak=True, metadata=None)
service.speak_message(text, metadata=None, wait=False)
service.stop_speaking()
service.run()
service.close()
```

## 事件协议

稳定事件字符串：

```text
listening
recognized
thinking
reply
speaking
idle
error
```

`Presenter.emit()` 可能来自服务线程或 Speech worker。GUI 消费方必须自行把事件转发到自己的 UI 主线程。

## 独立运行流程

```text
wakeword -> 唤醒确认 -> 录音 -> ASR
-> recognized -> thinking -> Gateway streaming -> speaking* -> reply -> idle
```

当前实现每次唤醒只执行一轮对话，不要把 follow-up 连续追问 loop 描述成现状。

## 嵌入式调用

纯 STT 使用 `transcribe_file()`，不需要 wakeword 或 Gateway。只要文字结果的 OpenClaw 对话使用 `ask_text(..., speak=False)`。不依赖 OpenClaw/ASR 的主动朗读使用 `speak_message()`。

## 本地 STT

默认：

```text
POST http://127.0.0.1:15900/stt
{"path": "C:/audio/input.wav"}
```

可用 `STT_HOST` / `STT_PORT` 覆盖。

## 配置

以 `.env.example` 与 `config/default.yaml` 为准。主要配置包括 OpenClaw URL/token/session、`OPENCLAW_HOME`、Gateway 超时、STT host/port、wakeword 模型、ASR 模型路径、音频阈值和 Windows SAPI voice。

## 工具脚本

当前支持：

- `scripts/tts_cli.py`
- `scripts/stt_endpoint_client.py`
- `scripts/list_audio_devices.py`
- `scripts/test_microphone.py`

## 边界

除非后续设计明确引入，否则不要添加或依赖 PySide6、Overlay 状态文件、文件型 TTS stop flag、macOS launchd/install 脚本、桌宠角色素材/气泡/动画、Gateway 主动推送监听或多轮 follow-up loop。

架构见 `docs/architecture.md`；本轮重构契约和实施记录见 `docs/PRD/2026-09-09-voice-core-sdk-refactor/`。
