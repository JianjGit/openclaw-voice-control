# 后端开发计划：Voice Core SDK Refactor

## 1. 目的

本文把本轮 Voice Core SDK 重构拆成可执行的后端开发阶段，作为实现、代码审查和验收的工作计划。

配套文档：

- `01-functional-prd.md`：功能范围与验收标准；
- `02-backend-design.md`：目标架构与后端设计；
- `03-public-api.md`：公开 Python API、事件协议与线程语义。

本计划的核心原则是：

> 先建立稳定的无 UI 核心协议和运行时控制，再迁移现有业务流程，最后删除 Overlay/macOS 历史耦合并补齐测试和文档。

不能通过“大改一把然后一起调通”的方式推进，因为本仓库同时承担独立运行与外部依赖两种角色，公共协议、线程模型和生命周期需要逐步锁定。

---

## 2. 最终目标

实现完成后的主要能力应为：

```text
Wakeword
  -> Record
  -> FunASR
  -> OpenClaw Gateway
  -> streaming sentences
  -> TTS
  -> idle
```

并同时提供可嵌入 API：

```text
transcribe_file(audio)
ask_text(text)
speak_message(text)
stop_speaking()
```

所有 UI 状态都通过：

```text
VoiceEvent -> Presenter.emit(event)
```

输出。

核心不得依赖 PySide6，也不得知道桌宠的立绘、气泡、动画或角色状态映射。

---

## 3. 开发原则

### 3.1 每个阶段必须可独立验证

每个阶段完成后都要保证代码至少可以：

- import；
- compile；
- 运行对应单元测试；
- 不引入未声明依赖。

### 3.2 公共协议优先稳定

优先实现并锁定：

- `VoiceEvent`；
- `VoiceEventKind`；
- `Presenter`；
- `NullPresenter`；
- `RuntimeControl`；
- `VoiceControlService` 的公开方法边界。

后续内部重构不得随意改变已经确定的事件字符串和外部调用语义。

### 3.3 不把消费方逻辑写入核心

核心代码、配置、测试和文档中不得出现特定桌宠素材、立绘路径、气泡组件或角色行为逻辑。

外部桌面应用只通过 Presenter 和公开 API 集成。

### 3.4 兼容能力优先于内部实现兼容

以下能力需要保留：

- wakeword；
- 录音与静音检测；
- FunASR；
- STT HTTP；
- OpenClaw Gateway；
- session JSONL fallback；
- 流式逐句 TTS；
- `scripts/tts_cli.py`。

但 Overlay 状态文件、macOS 启动方式、旧 stop flag 等内部实现不属于兼容目标。

---

## 4. 阶段 0：建立重构基线

### 目标

在修改核心行为之前，先为当前 `dev` 分支建立可对照基线。

### 工作项

1. 记录当前服务启动流程。
2. 记录当前配置项和环境变量。
3. 确认 Gateway 当前实际调用链。
4. 确认 STT HTTP 默认地址：`127.0.0.1:15900/stt`。
5. 确认 `scripts/tts_cli.py` 当前行为和依赖。
6. 建立最小 smoke test。

### 建议新增测试

```text
tests/test_config.py
tests/test_text.py
```

先覆盖无需硬件和模型的纯逻辑。

### 完成标准

- 能明确指出重构前的关键行为；
- 后续回归问题可以判断是旧行为还是新行为导致；
- 不进行大规模结构删除。

---

## 5. 阶段 1：实现事件协议与 Presenter

### 目标

建立与 UI 无关的标准事件层，并让 `service.py` 可以通过 Presenter 发布状态。

### 新增文件

```text
src/openclaw_voice_control/events.py
src/openclaw_voice_control/presenter.py
```

### events.py

实现：

```python
VoiceEventKind
VoiceEvent
```

事件类型：

```text
listening
recognized
thinking
reply
speaking
idle
error
```

### presenter.py

实现：

```python
Presenter
NullPresenter
ConsolePresenter
```

### service.py 改造

构造器增加：

```python
presenter: Presenter | None = None
```

默认：

```python
NullPresenter()
```

新增内部统一出口：

```python
_emit(event)
```

`_emit()` 负责捕获 Presenter 异常并记录日志，不能因为 GUI 消费方异常拖垮核心。

### 迁移策略

第一步可暂时同时保留旧 Overlay 更新和新 event emit，仅用于降低一次性修改风险。

例如：

```text
旧：update_overlay_state("thinking")
新：emit(VoiceEvent(kind=THINKING))
```

待事件覆盖完整后，再在后续阶段删除 Overlay 路径。

### 测试

新增：

```text
tests/test_events.py
tests/test_presenter.py
```

覆盖：

- enum/string 稳定；
- `NullPresenter` 无副作用；
- `ConsolePresenter` 可调用；
- Presenter 抛异常时核心继续工作；
- fake presenter 能记录事件顺序。

### 完成标准

- 核心已有统一事件出口；
- 默认运行不需要实际 UI Presenter；
- 自定义 Presenter 可以收到事件。

---

## 6. 阶段 2：拆出 RuntimeControl

### 目标

停止朗读和运行控制不再依赖 Overlay 状态文件。

### 新增文件

```text
src/openclaw_voice_control/runtime.py
```

### RuntimeControl 负责

```text
stop speech event
shutdown event
```

建议 API：

```python
request_stop_speech()
clear_stop_speech()
is_stop_speech_requested()
request_shutdown()
is_shutdown_requested()
```

内部使用：

```python
threading.Event
```

### 修改范围

- `service.py`
- `tts.py`

所有：

```text
stop_tts.flag
OverlayStateManager stop flag
```

调用逐步替换成 `RuntimeControl`。

### 注意

此阶段不急于删除 `state.py`，先确保所有控制职责完成迁移。

### 测试

```text
tests/test_runtime.py
```

覆盖：

- 多次 stop 安全；
- clear 后恢复；
- shutdown 幂等；
- 多线程读写可用。

### 完成标准

- TTS 停止不再读取或写入 Overlay 文件；
- 外部调用可以通过服务 API 请求停止朗读。

---

## 7. 阶段 3：重构 TTS 为独立 SpeechController

### 目标

解决 TTS 队列、COM 线程、事件输出和停止机制的职责混乱。

### 建议新增

```text
src/openclaw_voice_control/speech.py
```

### 职责拆分

```text
SpeechController
  - queue
  - worker lifecycle
  - stop/clear
  - wait_done
  - emit speaking/idle

WindowsTTS backend
  - SAPI5 voice
  - 真正 Speak
  - 可选声音提示
```

### 关键实现要求

1. SAPI COM 对象在 TTS worker 所在线程中创建并使用。
2. 不在主线程创建 COM 对象后交给后台线程调用。
3. 使用单一长寿命 worker，而不是每批句子临时起线程。
4. queue 顺序必须稳定。
5. `stop_speaking()` 要能：
   - 请求停止当前播放；
   - 清理待播放队列；
   - 最终恢复 idle。
6. `wait_done()` 要有确定语义。

### service.py 改造

由：

```python
self.tts.enqueue(sentence)
```

逐步变为：

```python
self.speech.enqueue(sentence, metadata=...)
```

### 测试

使用 fake backend，不依赖 Windows SAPI：

```text
tests/test_speech.py
```

覆盖：

- 队列顺序；
- speaking 事件；
- stop 清队列；
- wait_done；
- worker 异常恢复；
- 多次 enqueue 不重复启动 worker。

### 完成标准

- `tts.py` 不 import Overlay 配置；
- 实时朗读流程不依赖 PySide6 或状态文件；
- `SpeechController` 可以被 `speak_message()` 和 Gateway 流式回复共同复用。

---

## 8. 阶段 4：建立统一 ASR API

### 目标

主语音流程、STT HTTP 和外部调用统一使用同一个 ASR 入口和锁。

### service.py 新增公开方法

```python
def transcribe_file(path, *, metadata=None) -> str:
    ...
```

内部：

```python
with self._asr_lock:
    return self.asr.transcribe(path)
```

### 修改调用方

#### 主语音流程

不得再直接：

```python
self.asr.transcribe(...)
```

必须调用：

```python
self.transcribe_file(...)
```

#### STT HTTP

同样调用：

```python
service.transcribe_file(audio_path)
```

### 可选拆分

如果 `service.py` 继续过大，可将 HTTP Server 拆到：

```text
src/openclaw_voice_control/stt_server.py
```

但第一目标是统一调用链，而不是为了文件数量做抽象。

### 测试

```text
tests/test_transcription.py
tests/test_stt_server.py
```

使用 fake ASR 验证：

- 主调用与 HTTP 都走相同方法；
- 并发调用被锁串行化；
- 无效路径错误明确。

### 完成标准

- 仓库核心不存在绕过统一 ASR lock 的路径。

---

## 9. 阶段 5：建立统一 OpenClaw 文本对话 API

### 目标

把“发送文字给 OpenClaw + 流式朗读 + 事件输出”从唤醒词主循环中抽成可复用方法。

### service.py 新增

```python
def ask_text(
    text,
    *,
    speak=True,
    metadata=None,
) -> str:
    ...
```

### 正常事件顺序

`speak=True`：

```text
thinking
-> speaking (0..N)
-> reply
-> idle
```

`speak=False`：

```text
thinking
-> reply
-> idle
```

### 语音流程改造

原来：

```text
ASR
-> client.ask_streaming
-> TTS
```

改为：

```text
ASR
-> recognized event
-> ask_text(user_text)
```

### 错误策略

公共 `ask_text()`：

- 对嵌入式调用保留异常；
- emit `error`；
- cleanup 后回到 `idle`。

独立常驻 `run()`：

- 捕获单轮异常；
- 记录日志；
- 继续等待下一次唤醒。

### 测试

```text
tests/test_service_ask_text.py
```

覆盖：

- speak true/false；
- 完整 reply 返回；
- 事件顺序；
- Gateway 失败；
- 空输入；
- metadata 透传。

### 完成标准

- 外部应用无需麦克风即可通过 `ask_text()` 使用 OpenClaw。

---

## 10. 阶段 6：实现外部主动朗读 API

### 目标

让任意外部消费者使用同一 TTS 管线主动发话。

### service.py 新增

```python
def speak_message(
    text,
    *,
    metadata=None,
    wait=False,
) -> None:
    ...
```

以及：

```python
def stop_speaking() -> None:
    ...
```

### 要求

`speak_message()` 不依赖：

- Gateway；
- ASR；
- wakeword；
- recorder。

它只复用：

```text
SpeechController
Presenter
RuntimeControl
```

### 事件

默认：

```text
speaking -> idle
```

不把所有主动朗读都伪装成 `reply`。

### 测试

```text
tests/test_service_speak_message.py
```

覆盖：

- 独立调用；
- metadata；
- stop；
- wait；
- 空文本。

### 完成标准

- 外部桌面应用可直接触发核心朗读而无需启动完整 wakeword loop。

---

## 11. 阶段 7：重构完整 VoiceControlService 主循环

### 目标

让完整 wakeword 服务成为“组合公共能力”的 orchestrator，而不是另一套实现。

### 目标调用结构

```text
run()
  -> wait wakeword
  -> wake ack
  -> record
  -> transcribe_file()
  -> emit recognized
  -> ask_text()
  -> return wakeword idle
```

### 重点修复

1. 录音 prepared stream 不重复 `start()`。
2. wakeword 暂停/恢复与模型销毁分离，避免每轮重新加载模型。
3. ASR 主路径使用统一锁。
4. shutdown 能打断服务循环。
5. 所有资源在 `close()` 中幂等释放。

### follow-up 策略

本轮默认保持当前 `dev` 行为：

```text
wakeword -> one turn -> idle
```

不在 UI 解耦重构阶段额外引入追问循环行为变更。

如果后续决定恢复 follow-up，应单独形成需求和状态机设计，并复用同样的公共 API。

### 测试

使用 fake wakeword、recorder、ASR、client、speech：

```text
tests/test_service_loop.py
```

验证一轮完整状态：

```text
listening
recognized
thinking
speaking...
reply
idle
```

### 完成标准

- wakeword 独立版继续工作；
- 主循环不包含任何 Overlay 写状态逻辑。

---

## 12. 阶段 8：稳定 Gateway WebSocket 与流式回复

### 目标

在公共对话 API 稳定后，修复当前 Gateway 实现中影响流式 TTS 的可靠性问题。

### 重点工作

1. `send_timestamp` 在发送前记录，而不是 ACK 后记录。
2. ACK timeout 使用配置值。
3. 总响应 timeout 使用配置值。
4. session path 不再硬编码某台机器的盘符。
5. 增加 OpenClaw home/session dir 配置。
6. 不全局删除 process 的 proxy 环境变量。
7. WS 与 session fallback 不共享脆弱的 `full_text` 前缀状态。
8. 流式句子只 emit 一次。
9. callback 不为每句新建 daemon thread。
10. 修复 WebSocket `close()` 生命周期。
11. 处理接收响应时丢弃非预期 event 的问题。

### 推荐内部结构

将：

```text
transport event
session fallback text
sentence splitter
stream deduper
```

尽量拆成可单测的小函数或类。

### 测试

```text
tests/test_gateway_streaming.py
tests/test_gateway_session_fallback.py
```

使用 JSONL fixture 和 fake WS event。

重点验证：

- 相同回复不重复；
- fallback 可接管；
- 两个来源内容不完全一致时不会重复读；
- 句子顺序稳定。

### 完成标准

- 流式 TTS 不依赖真实 Gateway 才能做逻辑测试；
- session fallback 路径可配置。

---

## 13. 阶段 9：彻底删除 Overlay 核心依赖

### 目标

在事件、运行控制和语音 API 已经稳定后，移除旧 UI 实现。

### 删除

```text
src/openclaw_voice_control/overlay_app.py
src/openclaw_voice_control/state.py
run_overlay.bat
```

以及 Overlay 专属运行内容。

### config.py

删除：

```text
OverlayConfig
config.overlay
state_file
stop_flag_file
poll_interval_ms
```

### dependencies

从核心依赖删除：

```text
PySide6
```

同时检查并清理仅 Overlay 使用的其他依赖。

### pyproject.toml

删除：

```text
openclaw-overlay entry point
```

更新项目 description，不再写 macOS companion/overlay。

### 验证

仓库搜索：

```text
PySide6
overlay_app
OverlayStateManager
config.overlay
stop_tts.flag
```

核心代码应无残留。

### 完成标准

- `pip install -e .` 不安装 PySide6；
- CLI 在没有 PySide6 的环境可 import 和运行。

---

## 14. 阶段 10：清理旧平台脚本和配置

### 目标

让仓库结构真正对应 Windows Voice Core SDK，而不是继续保留大量 macOS 历史壳。

### 删除候选

```text
launchagents/
scripts/deploy_macos.command
scripts/deploy_macos.sh
scripts/install_macos.sh
scripts/uninstall_macos.command
scripts/uninstall_macos.sh
scripts/restart_service.command
scripts/restart_service.sh
scripts/start_overlay.sh
scripts/start_service.sh
scripts/build_host_apps.sh
scripts/openclaw_host_launcher.m
```

根据实际内容再确认 `doctor.sh` 是否改为 Windows 诊断或删除。

### 保留

```text
scripts/tts_cli.py
scripts/stt_endpoint_client.py
scripts/list_audio_devices.py
scripts/test_microphone.py
```

### run_service.bat

改为可移植脚本：

优先：

```text
.venv\Scripts\python.exe
```

必要时 fallback：

```text
python
```

不得硬编码个人 Python 安装盘符或仓库路径。

### .env.example

清理：

- Overlay Python path；
- 过期 HTTP API 配置；
- 机器绝对路径。

补充：

- `OPENCLAW_WS_URL`；
- `OPENCLAW_SESSION_KEY`；
- OpenClaw home/session path；
- STT HTTP host/port，如实现配置化。

### 完成标准

- fresh clone 不要求修改代码中的本机路径；
- Windows 安装路径可配置。

---

## 15. 阶段 11：依赖整理

### 目标

让依赖与真实代码一致。

### 检查项

当前需要重点确认：

- `websockets`：Gateway 实际使用，应进入正式依赖；
- `websocket-client`：若不再使用则删除；
- `pywin32`：Windows SAPI COM 显式依赖；
- `requests`：旧 HTTP OpenClaw client 移除后，若核心不再使用可删除；
- `pyttsx3`：若没有实际使用可删除；
- `PySide6`：删除；
- `edge-tts`：因 `scripts/tts_cli.py` 保留，需要决定放主依赖还是 optional/dev/tool extra；
- Porcupine 相关依赖：可考虑 optional extra，但不在第一步强制改变安装体验；
- FunASR / torch / torchaudio：继续作为 ASR 主能力依赖，或后续评估 optional extra。

### 目标

尽量区分：

```text
core runtime
tool extras
optional wakeword backends
dev/test dependencies
```

但不要为了 dependency extras 重构阻塞核心架构迁移。

---

## 16. 阶段 12：公共导入面与版本契约

### 目标

让外部仓库不需要 import 内部实现文件。

### __init__.py

导出：

```python
VoiceControlService
VoiceEvent
VoiceEventKind
Presenter
NullPresenter
ConsolePresenter
```

可评估是否公开：

```python
RuntimeControl
```

### 不承诺稳定的内部模块

例如：

```text
gateway_ws.py
asr.py
tts backend
wakeword backend
```

这些允许后续内部重构。

### 完成标准

外部消费者的典型 import：

```python
from openclaw_voice_control import VoiceControlService, VoiceEvent
```

无需依赖内部路径。

---

## 17. 阶段 13：测试体系完善

### 单元测试

目标覆盖：

```text
config
events
presenter
runtime
speech
text
transcription
gateway parsing
gateway dedupe
service public APIs
service event order
```

### 不要求 CI 做真实硬件测试

CI 不应依赖：

- 麦克风；
- Windows 实际音频设备；
- SAPI 真正发声；
- 本地 OpenClaw；
- FunASR 模型下载；
- wakeword 模型在线下载。

这些应使用 mock/fake。

### integration checklist

手工/本机验证：

1. wakeword；
2. microphone；
3. SenseVoice；
4. Gateway；
5. session fallback；
6. SAPI；
7. STT HTTP；
8. stop speaking；
9. 外部 Presenter；
10. `scripts/tts_cli.py`。

---

## 18. 阶段 14：CI 改造

### 当前问题

旧 CI 主要面向 Ubuntu/macOS shell 语法检查，不对应 Windows 主架构，也没有真正的核心测试。

### 新 CI 目标

至少包含：

```text
Python compile
unit tests
lint/format（如项目决定引入）
```

建议 Windows runner 为主：

```text
windows-latest
Python 3.11
```

如果依赖安装过重，可把纯单元测试使用最小依赖，避免 CI 自动下载巨大模型。

### 完成标准

PR 能自动发现事件协议、配置、Gateway parser、service API 等回归。

---

## 19. 阶段 15：文档全面同步

### README

重写：

```text
README.md
README.zh-CN.md
```

内容应覆盖：

- Windows 定位；
- 独立运行；
- SDK 嵌入方式；
- Presenter 示例；
- OpenClaw 配置；
- FunASR 配置；
- STT HTTP；
- TTS CLI。

### docs

将新实现同步到：

```text
docs/architecture.md
docs/modules/*
```

建议最终只保留一个 canonical architecture 文档，避免 `architecture.md` 与 `architecture-v2.md` 长期同时存在并冲突。

### SKILL

更新：

```text
skills/openclaw-voice-control/SKILL.md
skills/openclaw-voice-control/SKILL.zh-CN.md
```

删除 macOS/launchctl/overlay 内容。

---

## 20. 阶段 16：外部消费方集成验证

### 目标

验证这个仓库确实可以作为桌面应用依赖，而不是只在文档中“理论可嵌入”。

### 使用一个最小 FakeExternalPresenter

消费方实现：

```python
class FakeExternalPresenter:
    def emit(self, event):
        queue.put(event)
```

验证：

```text
ask_text()
transcribe_file()
speak_message()
stop_speaking()
```

### UI 框架边界验证

基础仓库只验证：

```text
Presenter emit 可从非 UI 线程调用
```

不引入 Qt 测试。

消费方自己的 Presenter 负责把事件转到其 UI thread。

### 完成标准

外部仓库只需要：

```text
依赖 openclaw-voice-control
实现 Presenter
调用公开 API
```

无需复制 ASR、Gateway、TTS 或 wakeword 代码。

---

## 21. 推荐提交拆分

为了方便 review，建议不要把整个重构压成一个巨型 commit。

推荐提交顺序：

```text
1. feat: add voice event and presenter protocol
2. refactor: add runtime speech control
3. refactor: isolate speech controller and windows tts backend
4. feat: expose unified transcription api
5. feat: expose text conversation and speech api
6. refactor: rebuild service loop on public core APIs
7. fix: harden gateway streaming and session fallback
8. refactor: remove overlay and pyside dependencies
9. chore: remove legacy macos runtime scripts
10. test: add core sdk unit and integration tests
11. docs: align repository documentation with voice core sdk
```

实际实现时可以按耦合程度合并相邻提交，但原则上每个提交应有一个可解释的主要目的。

---

## 22. 风险与应对

### 风险 1：TTS COM 线程问题

现状存在跨线程使用 COM 对象的风险。

应对：

- 由 Speech worker 自己初始化和销毁 SAPI COM。

### 风险 2：事件重复或顺序不稳定

Gateway streaming 和 session fallback 可能重复产出文本。

应对：

- 流式去重逻辑独立测试；
- 事件顺序测试；
- 不由多个异步线程直接 emit 相同回复片段。

### 风险 3：Presenter 阻塞核心

消费方可能在 `emit()` 中执行 UI 阻塞操作。

应对：

- 文档明确 `emit()` 必须快速返回；
- 消费方负责 UI thread bridge；
- 核心捕获 Presenter 异常。

### 风险 4：大模型依赖使 CI 过重

FunASR/PyTorch 安装和模型下载会拖慢 CI。

应对：

- 单元测试注入 fake backend；
- CI 不下载模型；
- 真实模型作为本机 integration validation。

### 风险 5：重构过程中破坏独立版

SDK 化后可能只关注外部调用，导致 wakeword 常驻版退化。

应对：

- `run()` 完整流程作为独立测试场景；
- 每阶段保留 standalone smoke test。

### 风险 6：为了桌宠过度设计事件协议

应对：

- 第一版固定 7 个基础 event kind；
- 非核心信息走 metadata；
- 不添加任何具体角色专属事件。

---

## 23. Definition of Done

本轮后端重构只有同时满足以下条件才算完成：

1. 核心不存在 PySide6 依赖。
2. `overlay_app.py` 和 `OverlayStateManager` 已移除。
3. `VoiceEvent` 和 `Presenter` 成为唯一展示扩展机制。
4. 独立版默认使用 `NullPresenter` 可运行。
5. `RuntimeControl` 不依赖文件 stop flag。
6. SpeechController 负责统一 TTS 队列和停止。
7. `transcribe_file()` 是 ASR 唯一服务级入口。
8. STT HTTP 复用 `transcribe_file()`。
9. `ask_text()` 可独立完成 OpenClaw 文本对话。
10. `speak_message()` 可独立完成主动朗读。
11. `stop_speaking()` 可由外部安全调用。
12. wakeword 独立主流程仍能完成一轮完整对话。
13. Gateway streaming 不重复朗读。
14. OpenClaw session 路径不硬编码个人机器路径。
15. `scripts/tts_cli.py` 保留并可继续使用。
16. macOS/Overlay 历史脚本和文档完成清理。
17. Windows fresh clone 文档与配置可执行。
18. 单元测试覆盖公共事件、运行控制、服务 API 和 Gateway 核心逻辑。
19. README、架构文档、模块文档、Skill 与代码一致。
20. 一个外部消费方可以仅通过 Presenter + 公共 API 完成集成。

---

## 24. 推荐实际执行顺序

最终建议严格按以下依赖顺序推进：

```text
Events / Presenter
        ↓
RuntimeControl
        ↓
SpeechController
        ↓
Unified ASR
        ↓
ask_text / speak_message public API
        ↓
VoiceControlService loop
        ↓
Gateway hardening
        ↓
Remove Overlay
        ↓
Remove legacy platform files
        ↓
Dependencies / CI / tests
        ↓
Docs
        ↓
External consumer validation
```

原因是 Overlay 删除应该发生在替代机制已经建立以后，而不是先删除旧机制再一边报错一边补新架构。

这能让每一步都处于可运行、可测试、可 review 的状态。
