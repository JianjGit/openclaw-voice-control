# OpenClaw Voice Control

面向 OpenClaw 的无界面 Windows Voice Core SDK 与独立语音服务。

本仓库只负责语音核心能力：唤醒词、录音、ASR、本地 STT HTTP、OpenClaw Gateway 对话、Windows SAPI 朗读、运行时停止/关闭控制，以及供外部应用接入的事件/Presenter API。这里**不包含**桌宠、Qt Overlay、角色立绘、气泡、动画或其他 UI 实现。

## 平台与运行时

- 目标平台：Windows。
- Python 3.11+。
- 实时 TTS 默认使用 Windows SAPI5。
- 本地 ASR 使用 FunASR SenseVoice。
- 默认唤醒词引擎为 openWakeWord；Porcupine 作为可选路线保留。
- OpenClaw 对话通过 Gateway WebSocket，并保留 session JSONL fallback。

## 安装

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
copy .env.example .env
```

编辑 `.env`，至少配置 OpenClaw token，以及你实际使用的本地模型路径。默认 YAML 为 `config/default.yaml`。

启动独立服务：

```powershell
.\run_service.bat
```

或：

```powershell
python -m openclaw_voice_control --config config/default.yaml --env-file .env
```

## 公开 Python API

```python
from openclaw_voice_control import (
    ConsolePresenter,
    NullPresenter,
    Presenter,
    VoiceControlService,
    VoiceEvent,
    VoiceEventKind,
)
from openclaw_voice_control.config import load_config

service = VoiceControlService(load_config(), presenter=NullPresenter())
```

稳定的服务入口：

```python
service.transcribe_file(path, metadata=None)          # -> str
service.ask_text(text, speak=True, metadata=None)     # -> str
service.speak_message(text, metadata=None, wait=False)
service.stop_speaking()
service.run()                                         # 阻塞式独立主循环
service.close()                                       # 幂等关闭
```

`transcribe_file()` 与 STT HTTP 共用同一个串行化 ASR 入口。`ask_text(..., speak=True)` 会把流式完整句子按顺序送入朗读队列；`speak=False` 只返回文字。`speak_message()` 不依赖 OpenClaw、ASR、wakeword 或录音。

## 事件与 Presenter

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

外部应用只需实现 Presenter：

```python
class MyPresenter:
    def emit(self, event):
        ui_queue.put(event)
```

`Presenter.emit()` 可能来自服务线程或 Speech worker。GUI 消费方必须自行把事件转发到自己的 UI 主线程。Presenter 抛出的异常会被核心隔离，不会拖垮语音服务。

最小接入示例见 `examples/external_presenter.py`。

## 常见事件流

带朗读的文本对话：

```text
thinking -> speaking (0..N) -> reply -> idle
```

不朗读：

```text
thinking -> reply -> idle
```

外部主动朗读：

```text
speaking -> idle
```

独立运行模式中的可恢复错误会在对应编排边界发 `error -> idle`。

## STT HTTP

默认接口：

```text
POST http://127.0.0.1:15900/stt
Content-Type: application/json

{"path": "C:/audio/input.wav"}
```

成功：

```json
{"text": "识别结果"}
```

host/port 可通过 `STT_HOST` / `STT_PORT` 或 YAML 配置。`scripts/stt_endpoint_client.py` 使用相同默认值，也支持命令行覆盖。

## 配置

主要环境变量：

```text
OPENCLAW_BASE_URL
OPENCLAW_WS_URL
OPENCLAW_TOKEN
OPENCLAW_AGENT_ID
OPENCLAW_SESSION_KEY
OPENCLAW_HOME
OPENCLAW_TIMEOUT_SECONDS
OPENCLAW_WS_TIMEOUT
STT_HOST
STT_PORT
WAKEWORD_PROVIDER
OPENWAKEWORD_MODEL_NAME
OPENWAKEWORD_MODEL_PATH
OPENWAKEWORD_THRESHOLD
PICOVOICE_ACCESS_KEY
WAKEWORD_FILE
SENSEVOICE_MODEL_PATH
SENSEVOICE_VAD_MODEL_PATH
VOICE_CONTROL_CONFIG
```

当前默认值见 `.env.example` 与 `config/default.yaml`。

## 工具脚本

本轮保留并维护：

- `scripts/tts_cli.py`：SAPI5 / 可选 edge-tts 的文件合成工具。
- `scripts/stt_endpoint_client.py`：本地 `/stt` 薄客户端。
- `scripts/list_audio_devices.py`：列出音频设备。
- `scripts/test_microphone.py`：麦克风录音诊断。

旧 Overlay、launchd、macOS install/deploy/restart 脚本和静音 TTS stub 已明确删除。

## 测试与 CI

`tests/` 覆盖事件协议、RuntimeControl、SpeechController 队列、ASR 串行锁、STT HTTP、`ask_text()`、`speak_message()`、单轮语音编排、Gateway 聚合/去重、配置、仓库清理、文本清理，以及外部 Presenter/API smoke test。

`.github/workflows/ci.yml` 已配置为 `windows-latest` + Python 3.11，执行安装、`compileall` 和 `pytest`。真实麦克风、SenseVoice 模型执行、openWakeWord、Windows SAPI 实际出声和真实 OpenClaw Gateway 连接仍属于机器级人工集成检查，不伪装成稳定单元测试。

## 文档入口

- 当前架构：`docs/architecture.md`
- 模块说明：`docs/modules/`
- 本轮重构设计/实施记录：`docs/PRD/2026-09-09-voice-core-sdk-refactor/`
- 外部接入示例：`examples/external_presenter.py`

当前明确不属于本仓库能力范围：桌宠 UI、角色素材、动画/状态映射、follow-up 连续对话 loop，以及未经后续设计定义的 OpenClaw 主动推送监听。
