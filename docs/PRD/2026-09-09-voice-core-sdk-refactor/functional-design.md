# 功能 PRD：无界面语音核心与可嵌入 SDK 重构

## 1. 背景

`openclaw-voice-control` 当前已经从上游 macOS 项目演化为 Windows 语音控制实现，但核心流程仍残留 Overlay、状态文件、PySide6 以及旧平台脚本等历史耦合。

与此同时，本仓库后续需要作为其他桌面应用的依赖，为外部应用提供以下能力：

- 将用户语音转成文字；
- 将文字发送给 OpenClaw；
- 接收 OpenClaw 的流式回复；
- 将回复逐句朗读；
- 把语音流程状态和回复内容以标准事件交给外部 UI；
- 允许外部应用主动要求核心朗读一段文本。

因此，本轮重构的产品定位从“带内置悬浮窗的语音应用”调整为：

> **OpenClaw 的可嵌入语音交互核心 + 可独立运行的 Windows 语音服务。**

核心仓库负责能力和协议，消费方仓库负责自己的界面、角色表现和交互逻辑。

---

## 2. 产品目标

### 2.1 主要目标

1. 去掉核心对 PySide6 和 `overlay_app.py` 的依赖。
2. 用统一的 `VoiceEvent` 事件协议替代 `service.py` 对 Overlay 状态文件的直接写入。
3. 提供 `Presenter` 扩展点，支持外部 UI 消费语音状态。
4. 提供默认 `NullPresenter`，保证无 UI 情况下可以直接运行。
5. 可选提供 `ConsolePresenter`，用于开发调试和日志观察。
6. 将 TTS 停止机制、队列和运行状态从 `OverlayStateManager` 中拆出。
7. 保留唤醒词、录音、FunASR、STT HTTP、OpenClaw Gateway、流式逐句朗读等现有能力。
8. 提供公开 Python API，使外部应用不依赖唤醒词也能使用核心能力。
9. 保留 `scripts/tts_cli.py`，继续作为独立 TTS 文件生成工具。
10. 删除或迁移不再属于本架构的 macOS/Overlay 运行脚本和文档。

### 2.2 非目标

本轮不负责：

- 实现任何具体桌宠的立绘、气泡、动画或窗口逻辑；
- 把具体角色状态映射写入本仓库；
- 让核心依赖 Qt、Tk、WebView 或其他 UI 框架；
- 为某一个消费方定制私有事件类型；
- 为保持历史 Overlay 行为而保留状态 JSON 或 `stop_tts.flag` 机制；
- 在本轮强制重写 `scripts/tts_cli.py` 的实现方式。

---

## 3. 用户与使用场景

### 3.1 独立运行用户

用户启动 `openclaw-voice-control` 后，系统以无界面方式运行：

1. 等待唤醒词；
2. 播放唤醒确认；
3. 录音；
4. FunASR 识别；
5. 发送文本给 OpenClaw；
6. 获取流式回复；
7. 逐句 TTS；
8. 返回 idle。

默认使用 `NullPresenter`，不显示任何 UI。

### 3.2 开发调试用户

启动时使用 `ConsolePresenter`，可在控制台看到：

- listening；
- recognized；
- thinking；
- reply；
- speaking；
- idle；
- error。

### 3.3 外部桌面应用

外部应用把本仓库作为 Python 依赖，引入 `VoiceControlService` 并提供自己的 Presenter。

外部 UI 可以根据事件自行实现：

- listening：切换监听状态；
- thinking：切换思考状态；
- reply：展示完整回复；
- speaking：保持对话展示并表现正在说话；
- idle：恢复默认状态；
- error：显示错误或静默恢复。

本仓库不规定这些状态如何渲染。

### 3.4 外部主动发话

外部应用可以直接调用：

```python
service.speak_message("你好")
```

无需经过唤醒词、录音、ASR 或 OpenClaw。

该调用必须复用统一 TTS 管线和事件流程。

### 3.5 外部文字对话

外部应用可以直接调用：

```python
reply = service.ask_text("今天天气怎么样？")
```

核心负责：

- thinking 事件；
- OpenClaw Gateway 请求；
- 流式逐句 TTS；
- speaking 事件；
- 最终 reply 事件；
- idle 事件。

### 3.6 外部语音转文字

外部应用可以调用：

```python
text = service.transcribe_file("input.wav")
```

STT HTTP 接口也应复用同一个内部方法，避免主流程与 HTTP 两套识别逻辑。

---

## 4. 功能需求

### FR-001：标准事件模型

系统必须提供一个轻量事件对象：

```python
VoiceEvent(
    kind=...,
    text="...",
    user_text="...",
    auto_hide_ms=4000,
    metadata={...},
)
```

基础事件种类：

- `listening`
- `recognized`
- `thinking`
- `reply`
- `speaking`
- `idle`
- `error`

事件协议必须与任何 UI 框架无关。

### FR-002：Presenter 扩展点

核心必须定义：

```python
Presenter.emit(event)
```

并至少提供：

- `NullPresenter`
- `ConsolePresenter`

Presenter 抛出的异常不得导致语音核心整体崩溃；核心应记录异常并继续运行，除非未来显式配置为 fail-fast。

### FR-003：无界面默认运行

CLI 与默认服务启动不得要求 PySide6。

安装基础依赖后，即使系统中没有任何 GUI 框架，也应能运行主服务。

### FR-004：统一 TTS 控制

TTS 的停止请求、播放队列、运行状态不得再依赖 Overlay 配置或状态文件。

需要提供清晰的运行时控制对象，例如：

```python
RuntimeControl
```

以及对外方法：

```python
service.stop_speaking()
```

### FR-005：统一 STT 入口

主语音流程、STT HTTP 和外部 API 必须复用同一个识别入口，并共享 ASR 锁。

避免出现一个路径加锁、另一个路径直接调用模型的情况。

### FR-006：统一 OpenClaw 文本对话入口

语音识别后的文本和外部直接输入的文本，应最终走同一个 `ask_text()` 管线。

### FR-007：主动朗读入口

提供公开方法：

```python
speak_message(text, metadata=None)
```

要求：

- 复用统一 TTS 队列；
- 发出 speaking/idle 等事件；
- 支持停止；
- 不依赖 OpenClaw；
- 不依赖麦克风；
- 不依赖唤醒词。

### FR-008：保留流式逐句 TTS

OpenClaw 返回流式内容时，应继续按完整句子尽早入队播放，而不是等待整段回复完成。

同时必须防止：

- 重复朗读；
- WS 与 session fallback 同时写入造成文本错位；
- 并发回调导致句子顺序混乱。

### FR-009：STT HTTP 保持兼容

继续支持本地 STT HTTP 接口，默认行为保持：

```text
POST http://127.0.0.1:15900/stt
```

请求体：

```json
{"path": "C:/path/to/audio.wav"}
```

响应：

```json
{"text": "识别结果"}
```

后续可把 host/port 配置化，但不应无必要破坏现有默认端口。

### FR-010：唤醒词能力保持

继续支持当前唤醒词后端：

- openWakeWord；
- Porcupine fallback。

本轮允许优化模型生命周期和音频设备使用，但不能删除唤醒词能力。

### FR-011：独立工具兼容

保留以下工具：

- `scripts/tts_cli.py`
- `scripts/stt_endpoint_client.py`
- `scripts/list_audio_devices.py`
- `scripts/test_microphone.py`

其中 `scripts/tts_cli.py` 暂不要求强制接入新的实时 TTS 控制器。

---

## 5. 事件语义

### listening

系统已进入语音输入状态，等待用户说话。

推荐字段：

- `text`：可选提示文字；
- `metadata.source`：例如 `wakeword`、`followup`、`external`。

### recognized

ASR 已完成。

- `text`：识别文本；
- `user_text`：同一轮用户文本。

### thinking

文本已准备发送或已经发送给 OpenClaw，当前等待回复。

- `user_text`：本轮用户输入。

### speaking

TTS 正准备或正在播放一段文本。

- `text`：当前朗读文本；
- `user_text`：若来源于一轮对话，则携带对应用户文本；
- `metadata`：可带 source、message_id、sequence 等。

### reply

OpenClaw 本轮完整回复已经确定。

- `text`：完整回复；
- `user_text`：本轮用户输入。

`reply` 与 `speaking` 不等价：`speaking` 可在完整回复确定之前多次出现。

### idle

核心已经结束当前交互，恢复等待或静默状态。

### error

当前阶段发生可报告错误。

建议 metadata 包含：

- `stage`；
- `exception_type`；
- 可选 `recoverable`。

不应把完整 traceback 作为面向 UI 的 `text`。

---

## 6. 兼容性要求

### 6.1 必须保持

- Windows 10/11 主运行环境；
- Python 3.11 目标；
- FunASR SenseVoice；
- 当前 Gateway WebSocket 思路；
- session JSONL fallback；
- Windows SAPI5 实时朗读；
- STT HTTP；
- wakeword；
- 环境变量 + YAML 配置；
- `scripts/tts_cli.py`。

### 6.2 允许破坏的旧兼容

以下内容不再作为兼容目标：

- Overlay 状态 JSON；
- `stop_tts.flag`；
- `overlay_app.py`；
- PySide6 运行依赖；
- macOS LaunchAgent；
- macOS host launcher；
- 旧 macOS 安装部署脚本。

---

## 7. 公开 API 目标

本轮期望形成稳定的 Python API：

```python
VoiceControlService(...)

service.run()
service.ask_text(text, *, speak=True, metadata=None) -> str
service.transcribe_file(path) -> str
service.speak_message(text, metadata=None) -> None
service.stop_speaking() -> None
service.close() -> None
```

具体签名见 `03-public-api.md`。

---

## 8. 质量要求

### 8.1 解耦

`src/openclaw_voice_control/` 的核心模块不得 import PySide6。

### 8.2 线程安全

必须明确：

- ASR 模型访问序列化；
- TTS worker 生命周期；
- Presenter 回调线程语义；
- Gateway 流式回调顺序。

### 8.3 可测试

不连接真实麦克风、SAPI、FunASR 模型、OpenClaw Gateway 时，也应能通过 mock/fake 覆盖核心流程测试。

### 8.4 错误恢复

一次对话失败不应让常驻服务直接退出。

失败时原则上：

```text
error -> idle
```

不可恢复的启动错误除外。

---

## 9. 验收标准

本轮实现完成后至少满足：

1. 基础安装不包含 PySide6。
2. `overlay_app.py` 与核心 Overlay 状态依赖被移除。
3. CLI 在无 GUI 环境下可以启动。
4. 自定义 Presenter 能收到完整事件序列。
5. `NullPresenter` 下语音流程行为正常。
6. `ask_text()` 可以在没有麦克风和 wakeword 的情况下工作。
7. `transcribe_file()` 与 STT HTTP 使用同一 ASR 锁。
8. `speak_message()` 可以独立触发 TTS。
9. `stop_speaking()` 不依赖文件系统 flag。
10. 流式逐句朗读保持可用且不重复。
11. `scripts/tts_cli.py` 继续存在并有文档说明。
12. 旧 macOS/Overlay 文档和脚本不再误导用户。
13. README、架构文档、模块文档与实际代码保持一致。
14. CI 至少覆盖事件协议、配置、文本处理、Gateway 解析和公共 API 的纯逻辑测试。

---

## 10. 后续可选能力

以下能力可以在核心接口稳定后继续扩展，但不作为本轮阻塞项：

- follow-up 连续对话模式；
- `reply_delta` 或更细粒度流式文本事件；
- 本地 IPC/消息队列接口；
- WebSocket/HTTP 外部控制接口；
- 多 Presenter 广播；
- 可插拔 TTS backend；
- 可插拔 ASR backend；
- OpenClaw 主动 push 消息监听。

这些扩展应继续遵守“核心定义能力和协议，消费方定义表现”的边界。
