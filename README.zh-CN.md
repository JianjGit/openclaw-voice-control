# OpenClaw Voice Control

面向 OpenClaw 的 Headless Windows Voice Core SDK 与独立语音服务。

OpenClaw Voice Control 把 Windows 机器变成可复用的语音能力层：支持唤醒词和按键说话两种输入方式、麦克风录音、SenseVoice/FunASR 识别、OpenClaw Gateway 对话、Windows SAPI5 流式朗读，并通过稳定的事件/API 接口供桌宠或其他外部应用接入。

> English: [`README.md`](README.md)

## 功能

- openWakeWord 唤醒词检测，可选 Porcupine。
- 运行时输入模式切换：`wakeword` / `push_to_talk`，不重启服务。
- `listen_once()` 按键说话 / 点击说话。
- 麦克风录音与静音自动结束判断。
- 本地 SenseVoice / FunASR 语音识别。
- 本地 `POST /stt` 文件识别 HTTP 接口。
- OpenClaw Gateway protocol v4 WebSocket 对话，并保留 session fallback。
- 流式回复按句进入 Windows SAPI5 TTS。
- FIFO 朗读队列，支持停止与关闭。
- Python SDK：语音输入、文本对话、音频文件识别、主动朗读、生命周期控制。
- 框架无关的 `Presenter` + `VoiceEvent`，用于桌宠、GUI 或其他外部程序。
- Windows standalone 独立运行模式。

核心仓库刻意不包含桌宠 UI、PySide6 Overlay、角色素材、气泡或动画逻辑。

## 运行要求

- Windows
- Python 3.11+
- 可访问且支持 **protocol v4** 的 OpenClaw Gateway
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

### 启动一个常驻 Voice Core

```powershell
.\run_service.bat
```

或：

```powershell
python -m openclaw_voice_control --config config/default.yaml --env-file .env
```

`run()` 只加载一次核心能力，之后同一个 ASR、Gateway、TTS、STT server 和 `VoiceControlService` 实例持续存在。

默认输入模式是 `wakeword`：

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

### 运行时切换输入模式

```python
service.set_input_mode("wakeword")
service.set_input_mode("push_to_talk")
```

`wakeword` 模式：

```text
wakeword engine 持续监听
```

`push_to_talk` 模式：

```text
wakeword engine 暂停
VoiceControlService.run() 仍然常驻
ASR / TTS / Gateway 保持加载
等待 listen_once()
```

切换时只 pause/resume wakeword，不销毁并重建模型。

`set_input_mode()` 返回：

- `True`：已经切换完成；
- `False`：当前正在录音 / ASR / Gateway / TTS，切换已经进入 pending，本轮结束后自动生效。

当前状态：

```python
service.get_input_mode()
service.get_pending_input_mode()
```

如果是 deferred switch，真正生效时 Presenter 会收到一个 `idle` 事件：

```python
{
    "source": "input_mode",
    "input_mode": "push_to_talk",
    "mode_change": "applied",
}
```

完整契约见 [`docs/input-modes.md`](docs/input-modes.md)。

### 按键说话 / 点击说话

常驻服务运行后，先切到：

```python
applied = service.set_input_mode("push_to_talk")
```

然后桌宠按钮、快捷键、托盘菜单等可以在线程池或后台 worker 调：

```python
ok = service.listen_once(
    speak=True,
    metadata={"source": "desktop_pet"},
)
```

流程：

```text
按钮 / 快捷键
  -> listen_once()
  -> recording
  -> SenseVoice / FunASR
  -> recognized
  -> OpenClaw Gateway
  -> streaming TTS（speak=True）
  -> reply
  -> idle
```

wakeword 路径和 `listen_once()` 共用同一把麦克风所有权锁，因此不会同时打开两个录音流。

如果没有启动 `run()`，`listen_once()` 仍然保留原先的一次性 SDK 使用方式。

### 其他 Python SDK 接口

```python
from openclaw_voice_control import NullPresenter, VoiceControlService
from openclaw_voice_control.config import load_config

service = VoiceControlService(
    load_config("config/default.yaml", ".env"),
    presenter=NullPresenter(),
)

reply = service.ask_text("你好", speak=True)
text = service.transcribe_file("C:/audio/input.wav")
service.speak_message("Voice Core 已启动。")
service.stop_speaking()
service.close()
```

主要公开方法：

```python
service.set_input_mode(mode)                          # -> bool
service.get_input_mode()                              # -> str
service.get_pending_input_mode()                      # -> str | None
service.listen_once(speak=True, metadata=None)         # -> bool
service.transcribe_file(path, metadata=None)          # -> str
service.ask_text(text, speak=True, metadata=None)     # -> str
service.speak_message(text, metadata=None, wait=False)
service.stop_speaking()
service.run()                                         # 常驻阻塞式服务循环
service.close()                                       # 幂等关闭
```

桌宠和其他 GUI 应用的接入方式见 [`docs/desktop-pet-integration.md`](docs/desktop-pet-integration.md)。

## 简单架构

主要技术：

- **Wakeword：** openWakeWord，可选 Porcupine
- **Audio：** `sounddevice` + NumPy
- **ASR：** FunASR + SenseVoice
- **对话：** OpenClaw Gateway protocol v4 WebSocket + session JSONL fallback
- **TTS：** Windows SAPI5
- **并发控制：** Python thread、lock、condition、queue、runtime event
- **外部接入：** 框架无关的 `VoiceEvent` / `Presenter`

整体关系：

```text
外部应用 / 桌宠
        │ API 命令
        │ VoiceEvent / Presenter
        ▼
VoiceControlService（常驻）
  ├─ set_input_mode()
  │    ├─ wakeword      -> wakeword resume
  │    └─ push_to_talk -> wakeword pause -> listen_once()
  ├─ 共用 microphone input lock
  ├─ Recording
  ├─ FunASR / SenseVoice
  ├─ STT HTTP Server
  ├─ OpenClaw Gateway
  ├─ SpeechController
  └─ Windows SAPI5
```

输入模式只决定“下一次麦克风输入由谁触发”，两条路径后面共用同一套 recording、ASR、Gateway 和 TTS 链路。

详细架构见 [`docs/architecture.md`](docs/architecture.md)。

## 项目目录

```text
openclaw-voice-control/
├─ config/       # 默认运行配置
├─ docs/         # 架构、模块、接入、测试与设计文档
├─ examples/     # 外部消费方示例
├─ scripts/      # TTS/STT/音频诊断工具
├─ skills/       # OpenClaw skill 文档
├─ src/          # openclaw_voice_control Python 包
├─ tests/        # 自动化测试
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
├─ input-modes.md
│  └─ wakeword / push-to-talk 运行时切换与安全规则
│
├─ desktop-pet-integration.md
│  └─ 桌宠 / bridge 如何调用 Voice Core API、消费事件
│
├─ modules/
│  ├─ cli-and-config.md    # 配置与启动
│  ├─ main-loop.md         # 常驻 run() 与输入模式编排
│  ├─ wakeword.md          # 唤醒词 provider 与生命周期
│  ├─ record.md            # 麦克风录音与静音判断
│  ├─ asr.md               # SenseVoice / FunASR
│  ├─ gateway-ws.md        # OpenClaw Gateway protocol v4、WebSocket 与回复聚合
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
   ├─ functional-design.md
   ├─ backend-design.md
   ├─ api-design.md
   └─ backend-dev-plan.md
```

## Presenter 事件

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

最小桥接示例：[`examples/external_presenter.py`](examples/external_presenter.py)。

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

自动化测试覆盖事件、RuntimeControl、朗读队列、ASR 串行化、STT HTTP、`listen_once()`、运行时输入模式切换、文本对话、主动朗读、wakeword 编排、Gateway protocol v4 握手/聚合/去重、配置，以及外部 Presenter/API 集成。

真实硬件验证见 [`docs/same-machine-test.md`](docs/same-machine-test.md)。
