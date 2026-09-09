# 后端设计：Voice Core SDK Refactor

## 1. 设计目标

本设计将当前语音控制程序拆成三个清晰层次：

```text
Application / Consumer
        │
        │ Presenter + Public API
        ▼
Voice Core Orchestration
        │
        ├── ASR
        ├── OpenClaw Gateway
        ├── Speech Controller
        ├── Recorder
        └── Wakeword
        │
        ▼
Platform / Runtime Backends
```

核心目标是让 `openclaw-voice-control` 同时满足：

- 作为 Windows 常驻语音服务独立运行；
- 作为 Python 依赖被其他桌面应用嵌入；
- 不依赖任何 UI 框架；
- 不知道外部应用如何展示角色、气泡或动画。

---

## 2. 当前问题

现有代码存在以下结构性问题：

1. `service.py` 直接依赖 `OverlayStateManager`。
2. TTS 的停止机制依赖 Overlay 的 stop flag。
3. `WindowsTTS` 接收 `OverlayConfig`，导致语音层和显示层绑定。
4. 主对话 ASR 与 STT HTTP 没有统一经过同一个加锁入口。
5. Gateway 的流式事件与 session fallback 同时参与文本拼接，容易产生重复或错位。
6. Gateway 内存在硬编码超时和本机 session 路径。
7. TTS COM 对象与播放线程职责不清晰。
8. `service.py` 既负责流程编排，又负责录音实现、HTTP server、UI 状态、异常提示，职责过重。
9. wakeword 的关闭操作可能连模型一起释放，导致每轮重新加载。
10. 当前代码存在录音流可能被重复 `start()` 的风险。

本轮重构会优先解决这些问题，但避免一次性把所有实现抽象成过度复杂的框架。

---

## 3. 目标模块结构

建议目标目录：

```text
src/openclaw_voice_control/
├── __init__.py
├── __main__.py
├── cli.py
├── config.py
│
├── events.py
├── presenter.py
├── runtime.py
│
├── service.py
├── recorder.py
├── stt_server.py
├── asr.py
├── wakeword.py
│
├── speech.py
├── tts.py
│
├── openclaw_client.py
├── gateway_ws.py
└── text.py
```

如果实现过程中发现 `recorder.py` 或 `stt_server.py` 过小，可以保留在 `service.py`，但职责边界仍按本设计执行。

---

## 4. 核心对象

### 4.1 VoiceEvent

位置：

```text
openclaw_voice_control/events.py
```

职责：

- 表达语音流程状态；
- 作为核心与 UI/消费者之间的稳定协议；
- 不携带任何 Qt、窗口、图片、角色状态概念。

建议：

```python
@dataclass(slots=True)
class VoiceEvent:
    kind: VoiceEventKind
    text: str = ""
    user_text: str = ""
    auto_hide_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
```

事件枚举：

```python
class VoiceEventKind(StrEnum):
    LISTENING = "listening"
    RECOGNIZED = "recognized"
    THINKING = "thinking"
    REPLY = "reply"
    SPEAKING = "speaking"
    IDLE = "idle"
    ERROR = "error"
```

---

### 4.2 Presenter

位置：

```text
openclaw_voice_control/presenter.py
```

职责：

- 接收 `VoiceEvent`；
- 将事件交给外部显示层；
- 核心不关心 Presenter 如何处理事件。

协议：

```python
class Presenter(Protocol):
    def emit(self, event: VoiceEvent) -> None:
        ...
```

内置实现：

#### NullPresenter

```python
class NullPresenter:
    def emit(self, event: VoiceEvent) -> None:
        return None
```

作为独立版默认值。

#### ConsolePresenter

只把事件写日志/控制台，便于调试。

### Presenter 异常隔离

建议由核心通过统一 helper 调用：

```python
def _emit(self, event: VoiceEvent) -> None:
    try:
        self.presenter.emit(event)
    except Exception:
        self.logger.exception("Presenter failed")
```

默认不允许 UI 的异常中断语音服务。

---

### 4.3 RuntimeControl

位置：

```text
openclaw_voice_control/runtime.py
```

职责只包含运行控制信号，不包含 UI 状态。

建议字段：

```python
class RuntimeControl:
    stop_speech_event: threading.Event
    shutdown_event: threading.Event
```

接口：

```python
request_stop_speech()
clear_stop_speech()
is_stop_speech_requested()
request_shutdown()
is_shutdown_requested()
```

禁止：

- 写 Overlay JSON；
- 创建 `stop_tts.flag`；
- 知道任何窗口状态。

---

### 4.4 SpeechController

位置：

```text
openclaw_voice_control/speech.py
```

职责：

- 管理实时 TTS 队列；
- 统一同步朗读与流式朗读；
- 管理 TTS worker；
- 响应停止请求；
- 发出 `speaking` 事件；
- 对 `WindowsTTS` backend 做生命周期管理。

建议边界：

```text
SpeechController
 ├── RuntimeControl
 ├── TTS backend
 ├── sentence queue
 └── Presenter emitter
```

`tts.py` 只实现 Windows TTS backend，不再负责 Overlay。

---

## 5. TTS 线程模型

### 5.1 设计原则

Windows SAPI5 的 COM 对象应由实际使用它的 worker 线程创建和持有，避免在主线程创建后跨线程调用。

推荐模型：

```text
Main/Service Thread
      │ enqueue()
      ▼
queue.Queue
      │
      ▼
Speech Worker Thread
      │
      ├── CoInitialize
      ├── create SAPI.SpVoice
      ├── emit speaking
      ├── Speak()
      └── CoUninitialize
```

### 5.2 队列语义

推荐：

- 单 worker；
- FIFO；
- 同一时刻只朗读一段；
- `wait_done()` 使用 `Queue.join()` 或显式 completion event；
- stop 请求应清空未播放项，并尽可能中断当前 SAPI 播放；
- 新的一轮播放前由上层明确清除 stop 状态。

### 5.3 `speak_message()`

主动朗读不能另起第二套播放逻辑，必须调用 `SpeechController`。

---

## 6. ASR 统一入口

当前需要建立唯一的 ASR 访问路径：

```python
def transcribe_file(self, audio_path: str | Path) -> str:
    with self._asr_lock:
        return self.asr.transcribe(str(audio_path))
```

调用方：

```text
Wakeword voice turn
        │
        └── transcribe_file()

STT HTTP
        │
        └── transcribe_file()

External consumer
        │
        └── transcribe_file()
```

这样可保证 FunASR 模型不会被多个线程并发调用。

---

## 7. OpenClaw 对话统一入口

`VoiceControlService.ask_text()` 应成为唯一文本对话编排入口。

推荐流程：

```text
ask_text(user_text)
      │
      ├── emit thinking
      │
      ├── OpenClawClient.ask_streaming()
      │        │
      │        └── on_sentence(sentence)
      │                 │
      │                 └── SpeechController.enqueue()
      │
      ├── wait TTS if speak=True
      │
      ├── emit reply
      │
      └── emit idle
```

注意：最终实现可以根据 UI 体验决定 `reply` 在 `wait_done()` 前还是后发出，但协议必须固定并写入 API 文档。

本设计建议：

1. 每个完整句子到达后尽快触发 `speaking`；
2. 完整回复确定后立刻触发 `reply`；
3. TTS 全部结束后触发 `idle`。

这样外部 UI 能在最终文本可用时立即显示完整气泡，同时朗读仍可能继续。

---

## 8. 独立语音流程

主循环建议只负责编排：

```text
wait wakeword
    ↓
acknowledge
    ↓
record
    ↓
transcribe_file
    ↓
recognized
    ↓
ask_text
    ↓
idle
```

不再出现：

```text
service.py -> OverlayStateManager.write()
```

所有展示状态都走：

```text
service.py -> _emit(VoiceEvent(...))
```

---

## 9. 录音模块

建议将以下逻辑从 `service.py` 移出：

- RMS 计算；
- start threshold；
- silence threshold；
- pending frames；
- start timeout；
- WAV 临时文件写入。

目标接口：

```python
class Recorder:
    def record_until_silence(self, prepared_stream=None) -> Path | None:
        ...
```

Recorder 不应发 UI 事件。

是否 listening/no_speech/error，由 service 层决定。

### 录音流交接

wakeword 到 recording 的切换必须确保 stream 只启动一次。

建议由 Recorder 对 stream 生命周期拥有唯一控制权，或明确 `prepared_stream` 参数表示“已经启动”还是“未启动”。不要保留模糊语义。

---

## 10. Wakeword 生命周期

当前 wakeword 在每轮切换录音时可能被完全 `close()`，从而释放模型。

目标：区分：

```text
pause/release microphone
```

和：

```text
destroy model / shutdown backend
```

可选接口：

```python
start()
pause()
resume()
close()
```

如果后端不适合做 pause/resume，也至少要保证模型实例不在每轮被重新加载。

---

## 11. Gateway WebSocket 重构

本轮保持现有总体方案：

```text
chat.send
   ├── WebSocket event.agent streaming
   └── local session JSONL fallback
```

但实现需要收敛。

### 11.1 时间戳

`send_timestamp` 应在发送请求之前记录，而不是 ACK 之后。

### 11.2 超时

移除硬编码：

- 10 秒 ACK；
- 120 秒总等待。

统一使用配置：

```text
ws_timeout
timeout_seconds
```

### 11.3 session 路径

移除硬编码盘符路径。

建议配置：

```yaml
openclaw:
  home_dir: "${OPENCLAW_HOME}"
  session_key: "agent:main:main"
```

session 目录由：

```text
OPENCLAW_HOME / agents / agent_id / sessions
```

推导。

### 11.4 双来源去重

WebSocket streaming 与 session fallback 不应直接共用一个可变 `full_text` 做简单前缀截取。

建议内部维护：

```text
ResponseAccumulator
```

职责：

- 接收 snapshot/delta；
- 判断是否为已有内容前缀扩展；
- 计算未发出的 suffix；
- 提取完整句子；
- 保证每句最多 emit 一次。

### 11.5 回调顺序

不要为每个句子新建 daemon thread。

优先在 Gateway 的同步控制线程内按顺序调用：

```python
on_sentence(sentence)
```

因为 `SpeechController.enqueue()` 本身应是快速、线程安全的。

### 11.6 连接关闭

修正 event loop 与 websocket close 的生命周期，确保 service `close()` 时连接真的被关闭。

### 11.7 proxy 环境变量

不得全局删除进程中的所有 proxy 环境变量。

应仅对 websocket 连接本身禁用代理，或在局部上下文处理。

---

## 12. STT HTTP Server

建议移动到：

```text
stt_server.py
```

接口可设计为：

```python
class STTServer:
    def __init__(self, transcribe: Callable[[Path], str], host, port): ...
    def start(): ...
    def close(): ...
```

好处：

- HTTP server 不直接知道 FunASR；
- 测试时可传 fake transcribe；
- service 可以在关闭时 `shutdown()` server；
- 避免匿名 daemon thread 无法回收。

默认仍保持：

```text
127.0.0.1:15900/stt
```

---

## 13. Service 生命周期

建议 `VoiceControlService` 支持显式：

```python
start_components()
run()
close()
```

最低要求：`close()` 幂等。

关闭顺序建议：

```text
request shutdown
   ↓
stop wakeword input
   ↓
stop STT HTTP server
   ↓
stop speech worker
   ↓
close gateway websocket
   ↓
release remaining audio resources
```

---

## 14. 配置设计

### 14.1 删除 OverlayConfig

以下配置应删除：

```yaml
overlay:
  enabled:
  state_file:
  stop_flag_file:
  poll_interval_ms:
```

### 14.2 新增 STT HTTP 配置

建议：

```yaml
stt_http:
  enabled: true
  host: 127.0.0.1
  port: 15900
```

### 14.3 OpenClaw 配置

保留/整理：

```yaml
openclaw:
  base_url: ...
  ws_url: ...
  token: ...
  agent_id: main
  session_key: agent:main:main
  timeout_seconds: 120
  ws_timeout: 10
  home_dir: ...
```

如果 `model` 和 `user` 已不参与 Gateway 请求，则从主配置中删除或标记 legacy。

### 14.4 TTS 配置

TTS 配置只包含 TTS 自身内容：

- engine；
- voice；
- wake ack；
- beep；
- post reply delay。

不能再包含 Overlay 路径。

---

## 15. 依赖清理

目标基础依赖中：

### 删除

- PySide6；
- 仅旧 HTTP client 使用的 requests（若确认主代码不再使用）；
- 仅旧实现使用的 pyttsx3（若确认没有调用）；
- 错配的 websocket-client（若代码实际使用 `websockets`）。

### 增加/修正

- `websockets`；
- `pywin32`（Windows SAPI5 COM）。

### 保留

- FunASR；
- numpy；
- sounddevice；
- openwakeword；
- torch/torchaudio；
- Porcupine 相关依赖（可考虑 optional extra）；
- `edge-tts`，因为 `scripts/tts_cli.py` 仍使用它。

---

## 16. 文件删除与保留策略

### 删除目标

- `src/openclaw_voice_control/overlay_app.py`
- `src/openclaw_voice_control/state.py`
- `run_overlay.bat`
- `launchagents/`
- macOS install/deploy/start/restart/uninstall scripts
- macOS host launcher
- `scripts/tts_simple.py`

### 保留

- `scripts/tts_cli.py`
- `scripts/stt_endpoint_client.py`
- `scripts/list_audio_devices.py`
- `scripts/test_microphone.py`

---

## 17. 测试设计

### 17.1 Event tests

使用 `RecordingPresenter`：

```python
class RecordingPresenter:
    events: list[VoiceEvent]
```

验证正常对话大致顺序：

```text
recognized
thinking
speaking...
reply
idle
```

录音入口还应包含：

```text
listening
```

### 17.2 Presenter isolation

fake Presenter 主动抛异常，service 不应崩溃。

### 17.3 ASR lock

并发触发 `transcribe_file()` 与 STT HTTP，fake ASR 应证明同一时间只有一个调用进入。

### 17.4 Speech queue

验证：

- FIFO；
- wait_done；
- stop clears pending；
- speaking 事件顺序。

### 17.5 Gateway accumulator

使用 fixture 模拟：

- WS 连续 snapshot；
- fallback 先到/后到；
- 重复文本；
- 多句一次到达；
- 最后一句没有终止标点。

确保不重复回调。

### 17.6 API tests

验证以下方法在没有真实硬件时可以通过 fake backend 单测：

```text
ask_text
transcribe_file
speak_message
stop_speaking
close
```

---

## 18. 迁移顺序

建议按以下顺序实施：

1. 新增 `events.py`、`presenter.py`、`runtime.py`。
2. 把 `service.py` 的 Overlay 写入替换为 `_emit()`。
3. 新建 SpeechController，迁移 TTS queue 和 stop 控制。
4. 删除 OverlayStateManager 依赖。
5. 提取 `transcribe_file()`，统一 ASR lock。
6. 提取 `ask_text()` 与 `speak_message()` 公共入口。
7. 清理录音/wakeword 生命周期问题。
8. 稳定 Gateway streaming + fallback。
9. 删除 Overlay/PySide6/macOS 历史文件。
10. 更新配置与依赖。
11. 补单元测试和 Windows CI。
12. 最后统一 README、architecture、module docs 和 skill 文档。

这样可以减少一次性大改导致难以定位回归的风险。

---

## 19. 与外部桌面应用的边界

本仓库允许外部应用：

```python
service = VoiceControlService(
    config=config,
    presenter=CustomPresenter(...),
)
```

但是本仓库禁止出现：

- 某个具体角色名的 Presenter 实现；
- 立绘文件路径；
- 气泡定位；
- 动画状态机；
- Qt signal；
- 桌宠窗口生命周期。

这些都属于消费方。

核心只保证：

```text
能力稳定 + 事件稳定 + API 稳定
```

这条边界应作为后续 code review 的架构约束。
