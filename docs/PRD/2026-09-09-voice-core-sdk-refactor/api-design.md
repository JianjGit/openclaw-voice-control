# Public API 设计：Voice Core SDK

## 1. 文档状态

本文定义本轮重构期望形成的公开 Python API 和事件协议。

这是设计契约，不代表当前 `dev` 分支已经全部实现。实现完成后，README 和模块文档应以这里的最终签名为准。

目标消费方包括：

- 独立 CLI/常驻服务；
- 外部桌面应用；
- 自动化工具；
- 测试程序；
- 后续可能的 IPC/HTTP adapter。

---

## 2. 导入面

期望最终可以从包顶层导入常用公共类型：

```python
from openclaw_voice_control import (
    VoiceControlService,
    VoiceEvent,
    VoiceEventKind,
    Presenter,
    NullPresenter,
    ConsolePresenter,
)
```

底层 backend 类型可以保留在子模块中，不承诺全部作为稳定公共 API。

---

## 3. VoiceEventKind

建议实现：

```python
from enum import StrEnum


class VoiceEventKind(StrEnum):
    LISTENING = "listening"
    RECOGNIZED = "recognized"
    THINKING = "thinking"
    REPLY = "reply"
    SPEAKING = "speaking"
    IDLE = "idle"
    ERROR = "error"
```

### 稳定性要求

上述字符串值属于外部消费协议。

一旦进入正式版本，不应随意改名。

如果未来增加新事件，旧 Presenter 应允许忽略未知事件，而不是因为新增事件导致崩溃。

---

## 4. VoiceEvent

建议签名：

```python
@dataclass(slots=True)
class VoiceEvent:
    kind: VoiceEventKind
    text: str = ""
    user_text: str = ""
    auto_hide_ms: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

### 字段说明

#### kind

当前事件类型。

#### text

事件的主要文本。

根据事件不同可能表示：

- recognized：识别出的文本；
- reply：完整 OpenClaw 回复；
- speaking：当前正在朗读的文本；
- error：适合展示给用户的简短错误信息；
- listening：可选提示语。

#### user_text

当前对话轮次对应的用户输入。

非对话事件可以为空。

#### auto_hide_ms

给 UI 的展示建议值。

语义：

- `0`：不提供自动隐藏建议；
- `> 0`：消费方可以在该毫秒数后隐藏临时表现。

这只是 hint，不是核心定时器。核心不会因为该字段自动关闭任何窗口。

#### metadata

扩展字段，适合放不值得升级为顶级字段的信息。

建议保留字段名：

```text
source
stage
message_id
sequence
recoverable
```

消费方必须允许出现额外 metadata。

---

## 5. Presenter Protocol

```python
from typing import Protocol


class Presenter(Protocol):
    def emit(self, event: VoiceEvent) -> None:
        ...
```

### 约束

1. `emit()` 应尽快返回。
2. 不应在 `emit()` 内执行长时间网络请求或阻塞操作。
3. 若 UI 框架要求主线程操作，Presenter 自己负责把事件投递到 UI 线程。
4. 核心不会 import Qt，也不会为 Qt 特殊处理 signal。
5. 核心默认捕获 Presenter 异常并写日志，避免显示层拖垮语音服务。

### 线程语义

设计目标：

> `Presenter.emit()` 可能从服务主线程或 Speech worker 触发，消费方不得假设调用一定发生在 UI 主线程。

因此 GUI Presenter 推荐只做线程安全的事件转发，例如：

```python
class CustomPresenter:
    def emit(self, event: VoiceEvent) -> None:
        self.ui_event_queue.put(event)
```

或者由消费方框架自己的 signal/event bridge 转发。

---

## 6. NullPresenter

```python
class NullPresenter:
    def emit(self, event: VoiceEvent) -> None:
        pass
```

默认独立模式使用该实现。

无 PySide6、无 GUI、无额外配置。

---

## 7. ConsolePresenter

建议：

```python
class ConsolePresenter:
    def __init__(self, logger: logging.Logger | None = None): ...
    def emit(self, event: VoiceEvent) -> None: ...
```

用于开发调试。

该实现不是正式 UI，只输出便于阅读的事件内容。

---

## 8. VoiceControlService 构造

建议：

```python
class VoiceControlService:
    def __init__(
        self,
        config: VoiceControlConfig,
        *,
        presenter: Presenter | None = None,
    ) -> None:
        ...
```

如果 `presenter is None`：

```python
NullPresenter()
```

### 依赖注入

为了测试，内部可以进一步支持 backend 注入，但不一定全部暴露为第一版公共 API。

例如测试构造器或 factory 可以注入：

- fake ASR；
- fake OpenClaw client；
- fake speech controller；
- fake recorder。

---

## 9. run()

```python
def run(self) -> None:
    ...
```

启动完整独立语音服务：

```text
load ASR
start STT HTTP
start wakeword
enter wakeword loop
```

### 阻塞语义

`run()` 为阻塞调用，直到：

- 外部请求 shutdown；
- 用户中断；
- 出现不可恢复启动错误。

外部 GUI 若不希望阻塞 UI 主线程，应自行在服务线程中调用 `run()`，或后续使用更细粒度 lifecycle API。

---

## 10. transcribe_file()

```python
def transcribe_file(
    self,
    path: str | Path,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    ...
```

### 功能

将音频文件转为文本。

### 保证

- 统一使用内部 ASR lock；
- 与 STT HTTP 共用同一个识别入口；
- 不要求 wakeword 已启动；
- 不要求 OpenClaw 可用；
- 不触发 TTS。

### 事件

默认建议不自动发 `recognized`，因为该方法可以被纯 STT 工具调用。

由更高层对话流程在需要时发 `recognized`。

如果未来需要，可通过独立参数控制是否 emit，但第一版保持职责单一。

### 错误

无效路径：

```python
FileNotFoundError
```

ASR 失败：保留明确异常并由调用方决定是否转换成 `error` 事件。

---

## 11. ask_text()

建议签名：

```python
def ask_text(
    self,
    text: str,
    *,
    speak: bool = True,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    ...
```

### 功能

直接向 OpenClaw 发起一轮文本对话。

不要求：

- 麦克风；
- wakeword；
- ASR。

### 正常流程

```text
thinking
  ↓
OpenClaw streaming
  ↓
speaking (0..N 次, speak=True 时)
  ↓
reply
  ↓
等待 TTS 结束
  ↓
idle
```

其中：

- `thinking.user_text = text`
- `speaking.text = 当前句子`
- `reply.text = 完整回复`
- `reply.user_text = text`

### speak=False

当：

```python
ask_text("...", speak=False)
```

流程变为：

```text
thinking -> reply -> idle
```

不得播放 TTS，也不发 speaking。

### 返回值

返回 OpenClaw 的完整最终文本：

```python
reply: str
```

### 空输入

建议：

```python
ValueError("text must not be empty")
```

不要向 Gateway 发送空消息。

### 错误事件

Gateway 失败时建议：

```text
error(stage="gateway") -> idle
```

同时 `ask_text()` 是否继续抛异常需要统一策略。

推荐公共 API：

- 对嵌入式调用保留异常，让上层可以处理；
- `run()` 的独立服务循环捕获异常、发事件并继续运行。

即：核心 API 不应因为“方便 CLI”而吞掉所有错误。

---

## 12. speak_message()

建议签名：

```python
def speak_message(
    self,
    text: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    wait: bool = False,
) -> None:
    ...
```

### 功能

让外部应用主动调用统一 TTS 管线。

### 不依赖

- OpenClaw；
- ASR；
- wakeword；
- recorder。

### 事件

至少：

```text
speaking -> idle
```

是否额外发 `reply`：

推荐默认**不发**。

原因：主动朗读文本不一定是 OpenClaw 回复，`reply` 应保留“对话回复完成”的语义。

消费方如果希望展示主动消息，可以根据：

```text
speaking.text
metadata.source
```

处理。

### metadata 示例

```python
service.speak_message(
    "提醒你喝水。",
    metadata={
        "source": "external",
        "message_id": "reminder-123",
    },
)
```

### wait

- `wait=False`：入队后返回；
- `wait=True`：等待该调用相关的朗读完成后返回。

如果实现无法安全支持“只等待本条消息”，第一版可只提供全队列 `wait_done()`，并把 `wait` 延后实现。

---

## 13. stop_speaking()

```python
def stop_speaking(self) -> None:
    ...
```

### 功能

请求停止当前 TTS，并清除未播放队列。

### 保证

- 不写文件；
- 不依赖 Overlay；
- 可被外部 UI 线程安全调用；
- 多次调用安全。

### 事件

停止完成后由 SpeechController / Service 最终回到：

```text
idle
```

避免出现 stop 后永远停留在 speaking 状态。

---

## 14. wait_until_idle() / wait_speech_done()

第一版可选公开：

```python
def wait_speech_done(self, timeout: float | None = None) -> bool:
    ...
```

用途：

- CLI；
- integration test；
- 外部应用需要串行播放时。

如果不公开，至少 SpeechController 内部必须有可靠的等待机制。

---

## 15. close()

```python
def close(self) -> None:
    ...
```

### 要求

- 幂等；
- 可以从 finally 调用；
- 请求服务退出；
- 停止 STT HTTP；
- 停止/关闭 wakeword；
- 停止 TTS worker；
- 关闭 Gateway websocket；
- 释放音频资源。

示例：

```python
service = VoiceControlService(config)
try:
    service.run()
finally:
    service.close()
```

---

## 16. 语音完整对话内部 API

公开 API 不一定需要暴露 `handle_one_turn()`。

内部建议整理为：

```python
def _handle_recorded_turn(self, wav_path: Path) -> bool:
    user_text = self.transcribe_file(wav_path)
    self._emit(recognized(...))
    self.ask_text(user_text)
```

这样语音入口只是对公开能力的组合，不形成第二套逻辑。

---

## 17. STT HTTP API

当前本地接口继续兼容。

### Endpoint

```text
POST /stt
```

默认：

```text
http://127.0.0.1:15900/stt
```

### Request

```json
{
  "path": "C:/audio/input.wav"
}
```

### Success

HTTP 200

```json
{
  "text": "你好"
}
```

### Error

建议未来统一 JSON 错误，而不是只依赖 `send_error()` HTML：

HTTP 400：

```json
{
  "error": "invalid_audio_path"
}
```

HTTP 500：

```json
{
  "error": "transcription_failed"
}
```

第一版实现可以先保持兼容，再逐步升级错误格式。

### 安全边界

默认只监听：

```text
127.0.0.1
```

不默认暴露到 LAN。

---

## 18. Presenter 使用示例

外部消费方示例：

```python
from queue import Queue

from openclaw_voice_control import Presenter, VoiceEvent


class AppPresenter:
    def __init__(self, queue: Queue[VoiceEvent]):
        self.queue = queue

    def emit(self, event: VoiceEvent) -> None:
        self.queue.put(event)
```

应用自己的 UI 层再消费：

```python
if event.kind == "listening":
    ...
elif event.kind == "thinking":
    ...
elif event.kind == "reply":
    ...
elif event.kind == "speaking":
    ...
elif event.kind == "idle":
    ...
```

本仓库不提供具体 UI 示例代码，以避免把某个 GUI 框架重新变成事实依赖。

---

## 19. 外部文本对话示例

```python
config = load_config(...)
service = VoiceControlService(config, presenter=AppPresenter(queue))

reply = service.ask_text(
    "帮我总结今天的计划",
    speak=True,
    metadata={"source": "desktop_app"},
)
```

---

## 20. 主动朗读示例

```python
service.speak_message(
    "连接已经恢复。",
    metadata={"source": "system_notice"},
)
```

---

## 21. 语音文件识别示例

```python
text = service.transcribe_file("recordings/input.wav")
```

---

## 22. Event 顺序约定

### 22.1 Wakeword 语音对话

推荐：

```text
listening
recognized
thinking
speaking *
reply
idle
```

其中 `speaking *` 表示 0 到多次。

如果 `speak=False`：

```text
listening
recognized
thinking
reply
idle
```

### 22.2 外部 ask_text

```text
thinking
speaking *
reply
idle
```

### 22.3 speak_message

```text
speaking
idle
```

### 22.4 错误

例如 Gateway 错误：

```text
thinking
error
idle
```

例如 ASR 错误：

```text
listening
error
idle
```

---

## 23. 并发约定

第一版服务建议采用“单活动对话”模型。

即同一 `VoiceControlService` 实例不保证多个 `ask_text()` 并发执行。

建议内部使用 turn lock：

```python
self._turn_lock = threading.Lock()
```

以避免：

- 两个 Gateway run 同时写 TTS 队列；
- Presenter 事件交叉；
- reply 与 speaking 属于不同轮次却混合。

### ASR

独立 `ASR lock`。

### TTS

单队列单 worker。

### Presenter

消费方负责自己的 UI 线程安全。

---

## 24. metadata 传递规则

公共调用中的 metadata 应尽量原样传给相关事件。

例如：

```python
metadata={
    "source": "desktop_app",
    "conversation_id": "abc",
}
```

核心可以增加自己的字段，但不得无必要覆盖消费方字段。

建议核心保留命名空间：

```text
core.*
```

或者使用明确顶级保留字段列表，具体实现时二选一。

---

## 25. 不属于 Public API 的内容

以下实现细节不应被外部消费方依赖：

- Overlay JSON；
- stop flag 文件；
- Gateway 内部 websocket 对象；
- session JSONL 文件解析类；
- SAPI COM 对象；
- FunASR AutoModel 实例；
- wakeword 内部 stream；
- 临时 WAV 路径；
- TTS queue 内部结构。

这允许基础仓库后续优化实现而不破坏消费方。

---

## 26. 版本兼容策略

在 API 稳定后建议：

- 新增可选参数：minor 版本；
- 新增事件 kind：minor 版本并在 changelog 说明；
- 删除/重命名公开方法：major 版本；
- 修改事件既有字段语义：major 版本；
- metadata 新增字段：patch/minor 均可。

当前仓库尚处于重构期，正式稳定版本前可以调整签名，但每次调整必须同步此文档。

---

## 27. 与具体桌宠项目的约束

消费方可以实现类似：

```python
class KazuhaPresenter:
    ...
```

但这个类必须留在消费方仓库。

`openclaw-voice-control` 不应出现：

- 角色名相关状态；
- 角色图片路径；
- 气泡尺寸；
- Qt widget；
- 动画映射。

核心 API 只负责提供足够稳定的语义事件，使任何桌面应用都可以自行映射。

---

## 28. 第一版建议最小公开面

如果希望控制 API 数量，第一版正式公开以下内容已经足够：

```python
VoiceControlService
VoiceEvent
VoiceEventKind
Presenter
NullPresenter
ConsolePresenter

VoiceControlService.run()
VoiceControlService.ask_text()
VoiceControlService.transcribe_file()
VoiceControlService.speak_message()
VoiceControlService.stop_speaking()
VoiceControlService.close()
```

其他 helper 保持内部实现，等真正有第二个调用场景时再公开。
