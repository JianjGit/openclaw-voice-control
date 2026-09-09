# OpenClaw Voice Control

面向 OpenClaw 的 Headless Windows Voice Core SDK 与独立语音服务。

OpenClaw Voice Control 把 Windows 机器变成可复用的语音能力层：既可以通过唤醒词进入语音对话，也可以由外部按钮/热键直接触发录音；随后统一走 SenseVoice/FunASR 识别、OpenClaw Gateway 对话、流式回复朗读，并通过稳定的事件/API 接口供桌宠或其他外部应用接入。

> English: [`README.md`](README.md)

## 功能

- openWakeWord 唤醒词检测，可选 Porcupine。
- `listen_once()` 按键说话 / 点击说话，无需唤醒词。
- 麦克风录音与静音自动结束判断。
- 本地 SenseVoice / FunASR 语音识别。
- 本地 `POST /stt` 文件识别 HTTP 接口。
- OpenClaw Gateway WebSocket 对话，并保留 session fallback。
- 流式回复按句进入 Windows SAPI5 TTS。
- FIFO 朗读队列，支持停止与关闭。
- Python SDK：一次性监听、文本对话、音频文件识别、主动朗读、生命周期控制。
- 框架无关的 `Presenter` + `VoiceEvent`，用于桌宠、GUI 或其他外部程序。
- Windows standalone 独立运行模式。

核心仓库刻意不包含桌宠 UI、PySide6 Overlay、角色素材、气泡或动画逻辑。

## 运行要求

- Windows
- Python 3.11+
- 可访问的 OpenClaw Gateway
- 本地 SenseVoice 模型文件
- 需要语音输入时使用麦克风

## 安装

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
copy .env.example .env
```

编辑 `.env`，至少配置 OpenClaw token，以及实际使用的本地 ASR 模型路径。默认运行配置在 [`config/default.yaml`](config/default.yaml)。

可选依赖：

```powershell
pip install -e ".[porcupine]"   # 可选 Porcupine 唤醒词
pip install -e ".[tts-cli]"     # tts_cli.py 的可选 edge-tts/comtypes 路线
```

## 快速使用

### 作为常驻唤醒语音服务运行

```powershell
.\run_service.bat
```

或：

```powershell
python -m openclaw_voice_control --config config/default.yaml --env-file .env
```

standalone 的主流程：

```text
wakeword
  -> recording
  -> SenseVoice / FunASR
  -> OpenClaw Gateway
  -> streaming reply
  -> SpeechController
  -> Windows SAPI5
  -> idle
```

### 不经过唤醒词，直接按键/点击说话

桌宠按钮、快捷键、托盘菜单等外部触发场景，可以在线程池或后台 worker 中直接调用：

```python
from openclaw_voice_control import NullPresenter, VoiceControlService
from openclaw_voice_control.config import load_config

service = VoiceControlService(
    load_config("config/default.yaml", ".env"),
    presenter=NullPresenter(),
)

ok = service.listen_once(
    speak=True,
    metadata={"source": "desktop_pet"},
)
```

`listen_once()` 会立即进入录音，不启动也不等待 wakeword，然后复用同一套后续链路：

```text
API 触发
  -> recording
  -> SenseVoice / FunASR
  -> recognized event
  -> OpenClaw Gateway
  -> streaming TTS（speak=True 时）
  -> reply / idle
```

返回值：

- `True`：这一轮录音、识别和对话流程成功完成；
- `False`：没有录到有效语音，或这一轮语音对话未能完成。

`run()` 和 `listen_once()` 是两种互斥的麦克风输入模式，不应同时运行。并发触发时会抛 `RuntimeError("voice input is already active")`，避免多个录音流同时抢麦克风。

### 其他 Python SDK 接口

```python
reply = service.ask_text("你好", speak=True)
print(reply)

text = service.transcribe_file("C:/audio/input.wav")
service.speak_message("Voice Core 已启动。")
service.stop_speaking()
service.close()
```

主要公开方法：

```python
service.listen_once(speak=True, metadata=None)         # -> bool
service.transcribe_file(path, metadata=None)          # -> str
service.ask_text(text, speak=True, metadata=None)     # -> str
service.speak_message(text, metadata=None, wait=False)
service.stop_speaking()
service.run()                                         # 阻塞式 wakeword 主循环
service.close()                                       # 幂等关闭
```

桌宠和其他 GUI 应用的接入方式见 [`docs/desktop-pet-integration.md`](docs/desktop-pet-integration.md)。

## 简单架构

主要技术：

- **Wakeword：** openWakeWord，可选 Porcupine
- **Audio：** `sounddevice` + NumPy
- **ASR：** FunASR + SenseVoice
- **对话：** OpenClaw Gateway WebSocket + session JSONL fallback
- **TTS：** Windows SAPI5
- **并发控制：** Python thread、lock、queue、runtime event
- **外部接入：** 框架无关的 `VoiceEvent` / `Presenter`

整体关系：

```text
外部应用 / 桌宠
        │ API 调用
        │ VoiceEvent / Presenter
        ▼
VoiceControlService
  ├─ listen_once() ───────┐
  ├─ Wakeword + Recording ├─> 共用一次语音对话链路
  ├─ FunASR / SenseVoice  │
  ├─ STT HTTP Server      │
  ├─ OpenClaw Gateway     │
  ├─ SpeechController     │
  └─ Windows SAPI5        │
```

standalone 唤醒主循环和 `listen_once()` 按键说话模式共用录音、ASR、Gateway 和朗读组件，所以桌宠不需要再实现一套语音链路。

详细架构见 [`docs/architecture.md`](docs/architecture.md)。

## 项目目录

```text
openclaw-voice-control/
├─ config/       # 默认运行配置
├─ docs/         # 架构、模块、接入、测试与设计文档
├─ examples/     # 外部消费方最小示例
├─ scripts/      # TTS/STT/音频诊断工具
├─ skills/       # OpenClaw skill 文档
├─ src/          # openclaw_voice_control Python 包
├─ tests/        # 自动化测试，包括 listen_once 接口覆盖
├─ .github/      # GitHub Actions workflow
├─ .env.example  # 环境变量模板
├─ pyproject.toml
└─ run_service.bat
```

## 文档导航

```text
docs/
├─ architecture.md
│  └─ 整体运行架构、线程、所有权和生命周期
│
├─ desktop-pet-integration.md
│  └─ 桌宠 / 外部 GUI 如何调用 Voice Core API、消费事件，包括 listen_once
│
├─ modules/
│  ├─ cli-and-config.md    # 配置与启动
│  ├─ main-loop.md         # run() 唤醒模式与 listen_once() 一次性输入模式
│  ├─ wakeword.md          # 唤醒词 provider 与生命周期
│  ├─ record.md            # 麦克风录音与静音判断
│  ├─ asr.md               # SenseVoice / FunASR
│  ├─ gateway-ws.md        # OpenClaw WebSocket 与回复聚合
│  ├─ tts.md               # Windows TTS 与 SpeechController
│  └─ events-and-text.md   # VoiceEvent 协议与文本清理
│
├─ same-machine-test.md
│  └─ Windows 真机麦克风 / ASR / wakeword / SAPI / Gateway 验证
│
├─ fresh-clone-validation.md
│  └─ 干净机器安装验证
│
├─ release-checklist.md
│  └─ 发布前检查
│
└─ PRD/2026-09-09-voice-core-sdk-refactor/
   ├─ functional-design.md # 功能行为定义
   ├─ backend-design.md    # 后端架构和职责
   ├─ api-design.md        # 本轮重构时的公开 API 契约
   └─ backend-dev-plan.md  # BE-01..BE-12 实施记录
```

如果你想知道“桌宠应该调用哪个接口”，先看 [`docs/desktop-pet-integration.md`](docs/desktop-pet-integration.md)。

## Presenter 事件

外部应用可以实现一个很薄的 Presenter：

```python
class MyPresenter:
    def emit(self, event):
        ui_queue.put(event)
```

稳定事件类型：

```text
listening
recognized
thinking
reply
speaking
idle
error
```

`Presenter.emit()` 可能来自服务线程、`listen_once()` worker 或 Speech worker，因此 GUI 应用必须把事件切回自己的 UI 主线程。

最小可运行桥接示例：[`examples/external_presenter.py`](examples/external_presenter.py)。

## 工具脚本

- [`scripts/tts_cli.py`](scripts/tts_cli.py) — SAPI5 / 可选 edge-tts 文件合成工具。
- [`scripts/stt_endpoint_client.py`](scripts/stt_endpoint_client.py) — 本地 `/stt` 客户端。
- [`scripts/list_audio_devices.py`](scripts/list_audio_devices.py) — 列出音频设备。
- [`scripts/test_microphone.py`](scripts/test_microphone.py) — 麦克风录音诊断。

更多说明见 [`scripts/README.md`](scripts/README.md)。

## 测试

```powershell
python -m pytest -q
```

自动化测试覆盖事件、RuntimeControl、朗读队列、ASR 串行化、STT HTTP、`listen_once()`、文本对话、主动朗读、standalone 编排、Gateway 聚合/去重、配置，以及外部 Presenter/API 集成。

真实硬件验证见 [`docs/same-machine-test.md`](docs/same-machine-test.md)。
