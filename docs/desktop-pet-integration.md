# 桌宠 / 外部 UI 接入指南

本项目只提供 Headless Voice Core，不包含桌宠窗口、角色立绘、动画、气泡或 UI 状态机。桌宠应用通过公开 Python API 和 `Presenter` 事件协议接入语音能力。

推荐架构：

```text
桌宠 / GUI
  ├─ 向 bridge 发送命令
  └─ 接收 bridge 转发的 VoiceEvent
          │
          ▼
bridge / worker
  └─ 持有唯一 VoiceControlService 实例
          │
          ▼
OpenClaw Voice Core
  ├─ set_input_mode()
  ├─ Wakeword / listen_once()
  ├─ Recording
  ├─ FunASR / SenseVoice
  ├─ OpenClaw Gateway
  ├─ SpeechController
  └─ Windows SAPI5
```

## 1. 接入边界

Voice Core 负责：

- `wakeword` / `push_to_talk` 输入模式；
- 唤醒词检测；
- `listen_once()` 一次性按键/点击说话入口；
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
- 麦克风按钮、快捷键、菜单等交互；
- “唤醒词模式 / 按键说话模式”的用户设置；
- 将模式切换命令发给 bridge；
- 是否显示用户识别文本、完整回复或当前朗读句子。

不要让 Voice Core 直接 import PySide6、Qt、桌宠窗口类或角色资源。

## 2. 推荐线程模型

`VoiceControlService.run()` 是常驻阻塞循环，`listen_once()` 也会阻塞直到一轮录音/识别/对话结束，所以都不要在 GUI 主线程直接运行。

```text
GUI 主线程
  │
  ├─ UI / 动画 / 气泡
  ├─ 模式切换按钮 / 麦克风按钮
  ├─ 读取 Presenter 事件
  │
  └──── command ───► bridge / worker threads
                          │
                          ▼
                 VoiceControlService.run()
                          │
                          └──── VoiceEvent ───► Presenter queue ───► GUI
```

`Presenter.emit()` 可能由 Voice Core 服务线程、`listen_once()` worker 或 Speech worker 调用，所以 GUI 必须把事件 marshal 回自己的 UI 主线程。

## 3. 最小 Presenter

```python
import queue

from openclaw_voice_control import VoiceEvent


class QueuePresenter:
    def __init__(self) -> None:
        self.events: queue.Queue[VoiceEvent] = queue.Queue()

    def emit(self, event: VoiceEvent) -> None:
        self.events.put(event)
```

完整示例见：

- [`examples/external_presenter.py`](../examples/external_presenter.py)

## 4. 初始化：只创建一次 Voice Core

```python
import threading

from openclaw_voice_control import VoiceControlService
from openclaw_voice_control.config import load_config

presenter = QueuePresenter()
config = load_config("config/default.yaml", ".env")
service = VoiceControlService(config, presenter=presenter)

voice_thread = threading.Thread(
    target=service.run,
    name="voice-core",
    daemon=True,
)
voice_thread.start()
```

推荐 bridge 在整个桌宠生命周期里只持有这一份 `service`。

以后切换输入方式时：

- 不重新 new `VoiceControlService`；
- 不退出 `run()`；
- 不重启 Python 进程；
- 不重新加载 ASR / TTS / Gateway。

## 5. 运行时切换输入模式

### 5.1 唤醒词模式

```python
applied = service.set_input_mode("wakeword")
```

生效后：

```text
wakeword engine resume
  -> 持续监听唤醒词
  -> 命中后 recording -> ASR -> Gateway -> TTS
```

### 5.2 按键说话模式

```python
applied = service.set_input_mode("push_to_talk")
```

生效后：

```text
wakeword engine pause
  -> run() 继续常驻
  -> ASR / TTS / Gateway 保持加载
  -> 等待桌宠调用 listen_once()
```

切换到 `push_to_talk` 时，wakeword 只是 `pause()`，不是 `close()`，所以已加载模型不会被卸载。

### 5.3 `set_input_mode()` 的返回值

```python
applied = service.set_input_mode(mode)
```

- `True`：已经切换完成；
- `False`：当前仍在录音、ASR、Gateway 对话或 TTS，已登记 pending，本轮结束后自动切换。

bridge 可以直接给桌宠回复：

```python
if applied:
    message = "语音输入模式已切换"
else:
    message = "当前对话结束后切换"
```

状态查询：

```python
service.get_input_mode()
service.get_pending_input_mode()
```

### 5.4 pending 最终生效通知

真正完成切换后，Voice Core 会发一个现有 `idle` 事件：

```python
{
    "source": "input_mode",
    "input_mode": "push_to_talk",
    "mode_change": "applied",
}
```

因此 bridge 可以在 `set_input_mode()` 返回 `False` 时先回复 pending，再等这个事件到来后通知桌宠“已完成切换”。

详细安全规则见 [`input-modes.md`](input-modes.md)。

## 6. 桌宠按钮触发 `listen_once()`

当前模式必须已经是 `push_to_talk`：

```python
service.get_input_mode() == "push_to_talk"
```

然后在 bridge worker 中：

```python
ok = service.listen_once(
    speak=True,
    metadata={
        "source": "desktop_pet",
        "request_id": "voice-001",
    },
)
```

调用后不会等待 wakeword：

```text
listen_once()
  -> listening
  -> recording
  -> ASR
  -> recognized
  -> thinking
  -> OpenClaw Gateway
  -> speaking (0..N)
  -> reply
  -> idle
```

参数：

- `speak=True`：OpenClaw 回复进入 TTS；
- `speak=False`：完成录音、ASR 和 OpenClaw 对话，但不朗读；
- `metadata`：向下游事件透传，便于 bridge 关联 request id。

返回：

- `True`：本轮成功完成；
- `False`：没有录到有效语音，或 recorded turn 未成功完成。

如果 `run()` 正在运行且当前仍为 `wakeword`，直接调用会得到：

```python
RuntimeError("listen_once requires push_to_talk input mode while run() is active")
```

这是一层安全保护，避免桌宠忘记切模式后又打开第二条麦克风链路。

## 7. 两条输入路径共用同一把麦克风锁

Voice Core 内部 wakeword 和 `listen_once()` 共用同一把 input lock。

```text
wakeword read / wake turn ----+
                              +---- microphone input lock
listen_once() ----------------+
```

因此模式切换顺序始终保证：

```text
wakeword -> push_to_talk
  -> 等当前 read / turn 结束
  -> input lock
  -> wakeword.pause()
  -> mode 生效
  -> listen_once() 才能使用麦克风
```

以及：

```text
push_to_talk -> wakeword
  -> 等 listen_once() 释放 input lock
  -> wakeword.resume()
  -> mode 生效
```

不会同时打开 wakeword stream 和 recording stream。

## 8. bridge 命令建议

桌宠只需要理解高层命令，不需要知道 openWakeWord / Porcupine 实现。

### 切模式

桌宠 -> bridge：

```json
{
  "type": "set_voice_input_mode",
  "mode": "push_to_talk"
}
```

bridge：

```python
def handle_set_voice_input_mode(command):
    applied = service.set_input_mode(command["mode"])
    return {
        "type": "voice_input_mode_result",
        "status": "applied" if applied else "pending",
        "mode": service.get_input_mode(),
        "pending_mode": service.get_pending_input_mode(),
    }
```

### 触发一次说话

桌宠 -> bridge：

```json
{
  "type": "listen_once",
  "request_id": "voice-001"
}
```

bridge worker：

```python
service.listen_once(
    speak=True,
    metadata={
        "source": "desktop_pet",
        "request_id": command["request_id"],
    },
)
```

## 9. VoiceEvent 怎么映射到桌宠

| kind | 主要字段 | 常见桌宠行为 |
| --- | --- | --- |
| `idle` | `metadata` | 回到待机动画；如果 `source=input_mode`，更新模式 UI |
| `listening` | `text` | 显示“正在听”，切监听动画 |
| `recognized` | `text`, `user_text` | 显示用户识别结果 |
| `thinking` | `user_text` | 显示思考状态 |
| `reply` | `text`, `user_text` | 显示完整 OpenClaw 回复 |
| `speaking` | `text` | 显示当前朗读句子，切说话动画 |
| `error` | `text`, `metadata` | 显示错误；可根据 `recoverable` 决定自动恢复 |

简单映射：

```python
def handle_voice_event(event):
    if event.metadata.get("source") == "input_mode":
        pet.set_voice_input_mode(event.metadata["input_mode"])
        return

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

`pet`、`bubble` 都属于桌宠项目，不应放回 Voice Core 仓库。

## 10. 桌宠主动发起文本对话

```python
reply = service.ask_text(
    "今天天气怎么样？",
    speak=True,
    metadata={"source": "desktop_pet", "request_id": "chat-001"},
)
```

- `speak=True`：返回文字并朗读；
- `speak=False`：只返回文字。

`ask_text()` 是阻塞调用，应放在线程池或后台 worker。

如果此时用户请求切换 input mode，切换会等这轮 Gateway/TTS 完成后再生效。

## 11. 桌宠主动让角色说一句话

```python
service.speak_message(
    "下载已经完成。",
    metadata={"source": "desktop_pet_notification"},
)
```

等待完成：

```python
service.speak_message("任务完成。", wait=True)
```

停止当前和排队中的朗读：

```python
service.stop_speaking()
```

如果朗读过程中请求切模式，同样会进入 pending，朗读结束后再切。

## 12. 识别已有音频文件

```python
text = service.transcribe_file(
    "C:/temp/input.wav",
    metadata={"source": "desktop_pet"},
)
```

该入口与 STT HTTP 共用同一个 ASR lock。

识别过程中请求切模式时，模式也会延后到识别完成后生效。

## 13. 不直接嵌入 Python 时：STT HTTP

```text
POST http://127.0.0.1:15900/stt
Content-Type: application/json

{"path": "C:/audio/input.wav"}
```

成功：

```json
{"text": "识别结果"}
```

当前 HTTP 接口只解决本地文件 ASR；模式切换、OpenClaw 对话、TTS 和 VoiceEvent 仍推荐通过 Python bridge 调 Voice Core SDK。

## 14. GUI 主线程消费事件

Voice Core 线程只写 queue：

```python
presenter.events.put(event)
```

GUI 主线程定期读取：

```python
def poll_voice_events():
    while True:
        try:
            event = presenter.events.get_nowait()
        except queue.Empty:
            break
        handle_voice_event(event)
```

### PySide6 / Qt

```text
Voice Core Presenter
  -> queue.Queue
  -> QTimer / bridge object
  -> Qt Signal
  -> QWidget / QML / 桌宠状态机
```

Qt click handler 不应直接执行阻塞的 `listen_once()` / `ask_text()`，应提交给 `QThreadPool`、Python worker thread 或其它后台任务。

## 15. 生命周期建议

推荐整个桌宠生命周期：

```text
创建 Presenter
  -> load_config
  -> 创建一次 VoiceControlService
  -> 后台启动一次 service.run()
  -> 运行期间反复 set_input_mode()
  -> push_to_talk 时反复 listen_once()
  -> 桌宠退出时 service.close()
```

关闭：

```python
service.close()
voice_thread.join(timeout=5)
```

`close()` 是幂等的，可以安全重复调用。

模式切换本身绝对不应该调用 `close()` 或重启进程。

## 16. 推荐桌宠状态机

```text
input mode:
  wakeword <---- set_input_mode() ----> push_to_talk
                                         |
                                         +-- listen_once()

conversation state:
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

input mode 与角色动画状态是两个不同维度，不要把 `push_to_talk` 当成 `VoiceEventKind`。

## 17. 常用 API 速查

```python
service.set_input_mode("wakeword")                   # -> bool
service.set_input_mode("push_to_talk")               # -> bool
service.get_input_mode()                              # -> str
service.get_pending_input_mode()                      # -> str | None
service.listen_once(speak=True, metadata=None)         # -> bool
service.transcribe_file(path, metadata=None)          # -> str
service.ask_text(text, speak=True, metadata=None)     # -> str
service.speak_message(text, metadata=None, wait=False)
service.stop_speaking()
service.run()                                         # persistent blocking service
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

## 18. 继续阅读

- 输入模式切换：[`input-modes.md`](input-modes.md)
- 整体架构：[`architecture.md`](architecture.md)
- 事件协议：[`modules/events-and-text.md`](modules/events-and-text.md)
- 常驻主循环：[`modules/main-loop.md`](modules/main-loop.md)
- Gateway：[`modules/gateway-ws.md`](modules/gateway-ws.md)
- ASR：[`modules/asr.md`](modules/asr.md)
- TTS：[`modules/tts.md`](modules/tts.md)
- Wakeword：[`modules/wakeword.md`](modules/wakeword.md)
- 配置：[`modules/cli-and-config.md`](modules/cli-and-config.md)
