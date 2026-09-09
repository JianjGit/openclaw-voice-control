# 桌宠 / 外部 UI 接入指南

本项目只提供 Headless Voice Core，不包含桌宠窗口、角色立绘、动画、气泡或 UI 状态机。桌宠应用通过公开 Python API 和 `Presenter` 事件协议接入语音能力。

适合的集成方式是：

```text
桌宠 / GUI
  ├─ 调用 VoiceControlService API
  └─ Presenter 接收 VoiceEvent
          │
          ▼
OpenClaw Voice Core
  ├─ Wakeword
  ├─ Recording
  ├─ FunASR / SenseVoice
  ├─ OpenClaw Gateway
  ├─ SpeechController
  └─ Windows SAPI5
```

## 1. 接入边界

Voice Core 负责：

- 唤醒词检测；
- 麦克风录音与静音结束判断；
- SenseVoice / FunASR 识别；
- OpenClaw Gateway 文本对话；
- 流式回复拆句后进入朗读队列；
- Windows SAPI5 TTS；
- STT HTTP 接口；
- `VoiceEvent` 事件输出；
- 停止朗读和关闭服务。

桌宠负责：

- UI 主线程；
- 角色动画；
- 气泡文本；
- `idle / listening / thinking / speaking / error` 到角色状态的映射；
- 鼠标、键盘、菜单等交互；
- 是否显示用户识别文本、完整回复或当前朗读句子。

不要让 Voice Core 直接 import PySide6、Qt、桌宠窗口类或角色资源。

## 2. 推荐线程模型

`VoiceControlService.run()` 是阻塞式独立主循环，所以不要在 GUI 主线程直接运行。

推荐：

```text
GUI 主线程
  │
  ├─ UI / 动画 / 气泡
  ├─ 定时读取事件队列，或通过 GUI framework signal 转发
  │
  └──── API 调用任务 ───► 后台线程
                         │
                         ▼
                  VoiceControlService
                         │
                         └──── VoiceEvent ───► Presenter ───► thread-safe queue
```

`Presenter.emit()` 可能由 Voice Core 服务线程或 Speech worker 调用，所以 GUI 必须把事件 marshal 回自己的 UI 主线程。

## 3. 最小 Presenter

`Presenter` 只要求一个方法：

```python
from openclaw_voice_control import VoiceEvent

class MyPresenter:
    def emit(self, event: VoiceEvent) -> None:
        ...
```

最简单、安全的做法是使用 `queue.Queue`：

```python
import queue

from openclaw_voice_control import VoiceEvent


class QueuePresenter:
    def __init__(self) -> None:
        self.events: queue.Queue[VoiceEvent] = queue.Queue()

    def emit(self, event: VoiceEvent) -> None:
        self.events.put(event)
```

完整可运行示例见：

- [`examples/external_presenter.py`](../examples/external_presenter.py)

## 4. 初始化 Voice Core

```python
from openclaw_voice_control import VoiceControlService
from openclaw_voice_control.config import load_config

presenter = QueuePresenter()
config = load_config("config/default.yaml", ".env")
service = VoiceControlService(config, presenter=presenter)
```

如果桌宠需要完整语音唤醒模式，把 `service.run()` 放到后台线程：

```python
import threading

voice_thread = threading.Thread(
    target=service.run,
    name="voice-core",
    daemon=True,
)
voice_thread.start()
```

`run()` 会加载 ASR、启动本地 STT HTTP server、启动 wakeword，并进入：

```text
wakeword
  -> 唤醒确认音
  -> recording
  -> ASR
  -> OpenClaw Gateway
  -> streaming TTS
  -> idle
```

## 5. VoiceEvent 怎么映射到桌宠

稳定事件类型：

| kind | 主要字段 | 常见桌宠行为 |
| --- | --- | --- |
| `idle` | `metadata` | 回到待机动画，隐藏或收起状态提示 |
| `listening` | `text` | 显示“正在听”，切监听动画 |
| `recognized` | `text`, `user_text` | 显示用户识别结果 |
| `thinking` | `user_text` | 显示思考状态 |
| `reply` | `text`, `user_text` | 显示完整 OpenClaw 回复 |
| `speaking` | `text` | 显示当前朗读句子，切说话动画 |
| `error` | `text`, `metadata` | 显示错误状态；可根据 `recoverable` 决定是否自动恢复 |

事件对象：

```python
@dataclass
class VoiceEvent:
    kind: VoiceEventKind
    text: str
    user_text: str
    auto_hide_ms: int
    metadata: Mapping[str, Any]
```

推荐只依赖稳定字段和 `kind`，不要让桌宠读取 Voice Core 内部对象。

### 一个简单的 UI 映射例子

```python
def handle_voice_event(event):
    kind = event.kind.value

    if kind == "idle":
        pet.set_state("idle")
    elif kind == "listening":
        pet.set_state("listening")
        bubble.show(event.text)
    elif kind == "recognized":
        bubble.show(event.text)
    elif kind == "thinking":
        pet.set_state("thinking")
    elif kind == "reply":
        bubble.show(event.text)
    elif kind == "speaking":
        pet.set_state("speaking")
        bubble.show(event.text)
    elif kind == "error":
        pet.set_state("error")
        bubble.show(event.text)
```

这里的 `pet`、`bubble` 都属于桌宠项目，不应放回 Voice Core 仓库。

## 6. 桌宠主动发起文本对话

如果用户在桌宠输入框输入文字，可以直接调用：

```python
reply = service.ask_text("今天天气怎么样？", speak=True)
```

- `speak=True`：返回文字，同时把流式回复送入 TTS；
- `speak=False`：只进行 OpenClaw 文本对话，不朗读。

推荐附带 metadata，方便桌宠区分来源：

```python
reply = service.ask_text(
    "帮我总结一下今天的事项",
    speak=True,
    metadata={"source": "desktop_pet", "request_id": "chat-001"},
)
```

`ask_text()` 是阻塞调用，不要直接放到 GUI 主线程。应在线程池或后台 worker 中调用，UI 状态通过 Presenter 事件更新。

## 7. 桌宠主动让角色说一句话

如果不需要经过 OpenClaw，只想让桌宠朗读通知：

```python
service.speak_message(
    "下载已经完成。",
    metadata={"source": "desktop_pet_notification"},
)
```

等待朗读完成：

```python
service.speak_message("任务完成。", wait=True)
```

停止当前和排队中的朗读：

```python
service.stop_speaking()
```

这组 API 适合：

- 桌宠主动通知；
- 定时提醒；
- 外部应用事件提示；
- 菜单中的“停止说话”按钮。

## 8. 桌宠识别已有音频文件

如果桌宠自己拿到了 WAV 文件：

```python
text = service.transcribe_file(
    "C:/temp/input.wav",
    metadata={"source": "desktop_pet"},
)
```

该调用与内置 STT HTTP server 使用同一个 ASR lock，因此不会和其它识别任务并发进入 SenseVoice。

## 9. 不直接嵌入 Python 时：使用 STT HTTP

独立服务默认提供：

```text
POST http://127.0.0.1:15900/stt
Content-Type: application/json

{"path": "C:/audio/input.wav"}
```

成功响应：

```json
{"text": "识别结果"}
```

如果桌宠不是 Python，或者希望把音频识别和 UI 进程解耦，可以使用这个接口。

注意：当前 HTTP 接口只解决本地文件 ASR；OpenClaw 对话、TTS 和事件接入仍推荐直接使用 Python SDK。

## 10. GUI 主线程消费事件

### 通用 queue 模式

Voice Core 线程只写 queue：

```python
presenter.events.put(event)
```

GUI 主线程使用自己的 timer / event loop 定期读取：

```python
def poll_voice_events():
    while True:
        try:
            event = presenter.events.get_nowait()
        except queue.Empty:
            break
        handle_voice_event(event)
```

### PySide6 / Qt 项目

Voice Core 仍然不要依赖 PySide6。Qt 项目可以在自己的代码中把 queue 事件转成 signal：

```text
Voice Core Presenter
  -> queue.Queue
  -> QTimer / bridge object
  -> Qt Signal
  -> QWidget / QML / 桌宠状态机
```

这样 Qt 生命周期、窗口线程和 Voice Core worker 不会互相污染。

## 11. 生命周期建议

启动：

```text
创建 Presenter
  -> load_config
  -> VoiceControlService
  -> 后台启动 service.run()
```

关闭桌宠时：

```python
service.close()
voice_thread.join(timeout=5)
```

`close()` 是幂等的，可以安全重复调用。

建议桌宠在以下场景统一调用 `close()`：

- 用户退出桌宠；
- Windows 注销/关机处理；
- 开发模式热重启前；
- Voice Core 线程异常退出后的清理流程。

## 12. 推荐的桌宠状态机

Voice Core 不规定 UI 状态机，但一般可以这样映射：

```text
idle
 ├─ listening
 │    └─ recognized
 │         └─ thinking
 │              ├─ speaking
 │              └─ reply
 │                   └─ idle
 ├─ speaking        # 主动 speak_message
 │    └─ idle
 └─ error
      └─ idle
```

不要假设所有事件都严格来自一个线程，也不要把动画完成作为 Voice Core 继续运行的前置条件。

## 13. 常用 API 速查

```python
service.transcribe_file(path, metadata=None)          # -> str
service.ask_text(text, speak=True, metadata=None)     # -> str
service.speak_message(text, metadata=None, wait=False)
service.stop_speaking()
service.run()                                         # blocking
service.close()                                       # idempotent
```

公开类型：

```python
from openclaw_voice_control import (
    ConsolePresenter,
    NullPresenter,
    Presenter,
    VoiceControlService,
    VoiceEvent,
    VoiceEventKind,
)
```

## 14. 继续阅读

- 整体架构：[`architecture.md`](architecture.md)
- 事件协议：[`modules/events-and-text.md`](modules/events-and-text.md)
- Standalone 主循环：[`modules/main-loop.md`](modules/main-loop.md)
- Gateway：[`modules/gateway-ws.md`](modules/gateway-ws.md)
- ASR：[`modules/asr.md`](modules/asr.md)
- TTS：[`modules/tts.md`](modules/tts.md)
- Wakeword：[`modules/wakeword.md`](modules/wakeword.md)
- 配置：[`modules/cli-and-config.md`](modules/cli-and-config.md)
- 完整 API 设计：[`PRD/2026-09-09-voice-core-sdk-refactor/api-design.md`](PRD/2026-09-09-voice-core-sdk-refactor/api-design.md)
