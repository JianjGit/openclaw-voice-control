# Voice Core SDK 后端开发计划

## 0. 实施状态

- [x] BE-01 事件模型与 Presenter 协议 — `BE-T01` 通过，提交 `1dd11b8`。
- [x] BE-02 RuntimeControl 与停止机制解耦 — `BE-T02` 通过，提交 `4d54bc1`。
- [ ] BE-03 SpeechController 与 Windows TTS 重构
- [ ] BE-04 统一 ASR 与 STT HTTP 调用链
- [ ] BE-05 统一 OpenClaw 文本对话入口
- [ ] BE-06 外部主动朗读 API
- [ ] BE-07 VoiceControlService 主循环迁移
- [ ] BE-08 Gateway WebSocket 与流式回复稳定化
- [ ] BE-09 配置、依赖与公开导入面整理
- [ ] BE-10 删除 Overlay 与旧平台残留
- [ ] BE-11 自动化测试与 Windows CI
- [ ] BE-12 文档校准与外部消费方集成验证

## 1. 实施原则

- 以 `functional-design.md`、`backend-design.md` 和 `api-design.md` 为本轮实现基线，不在开发过程中引入文档范围外的桌宠 UI 逻辑。
- 先建立稳定的事件协议、运行时控制和公开 API，再迁移现有语音主流程，最后删除 Overlay/macOS 历史实现。
- 每项任务完成后执行对应测试；编号映射见本文的 `BE-T01` 至 `BE-T12`。
- 保持现有 wakeword、录音、FunASR、STT HTTP、OpenClaw Gateway、session JSONL fallback 和流式逐句 TTS 能力，不顺手重构无关模块。
- `scripts/tts_cli.py` 本轮保留；除非为修复公共依赖或兼容问题所必需，不强制并入新的实时 TTS 管线。
- 核心仓库只定义能力、事件协议和扩展点；具体桌宠立绘、气泡、动画与状态映射全部留在消费方仓库。
- 公共 API 的异常语义、事件顺序和线程约定一旦实现并通过测试，不在后续任务中随意改名或改变默认行为。
- 硬件、模型和真实 OpenClaw 连接无法稳定放入 CI 的部分使用 fake/mock 覆盖，最终再做 Windows 真机闭环验证。

## 2. 开发任务

### BE-01 事件模型与 Presenter 协议

- 新增 `VoiceEventKind` 和 `VoiceEvent`，固定 `listening`、`recognized`、`thinking`、`reply`、`speaking`、`idle`、`error` 七类基础事件。
- 定义 `Presenter.emit(event)` 协议，并提供默认 `NullPresenter` 与调试用 `ConsolePresenter`。
- 为服务增加统一 `_emit()` 出口，捕获 Presenter 异常并记录日志，避免显示层异常拖垮语音核心。
- 明确 Presenter 的线程语义：消费方不得假设 `emit()` 一定运行在 GUI 主线程。
- 从包顶层导出稳定的事件和 Presenter 公共类型。

验收：无 PySide6 环境下可导入并构造默认服务；fake Presenter 能收到事件，Presenter 自身异常不会导致核心流程崩溃。对应 `BE-T01`。

### BE-02 RuntimeControl 与停止机制解耦

- 新增 `RuntimeControl`，使用 `threading.Event` 管理停止朗读和服务 shutdown 信号。
- 提供 `request_stop_speech()`、`clear_stop_speech()`、`is_stop_speech_requested()`、`request_shutdown()`、`is_shutdown_requested()` 等内部能力。
- 将 TTS 停止逻辑从 `OverlayStateManager`、`stop_tts.flag` 和 Overlay 配置中移出。
- 为 `VoiceControlService.stop_speaking()` 和后续 `close()` 提供统一控制基础。
- 保证多次 stop、clear、shutdown 调用幂等且可从外部线程安全调用。

验收：停止朗读不再读写 Overlay 状态文件或 flag 文件，多线程触发 stop/shutdown 行为稳定。对应 `BE-T02`。

### BE-03 SpeechController 与 Windows TTS 重构

- 新增独立 `SpeechController`，负责句子队列、worker 生命周期、停止、等待完成和 speaking 状态。
- 将 Windows SAPI5 作为底层 TTS backend，避免在主线程创建 COM 对象后交给后台线程使用。
- 让 SAPI COM 对象在实际 TTS worker 线程中创建和使用，并在 worker 生命周期结束时正确清理。
- 使用单一长寿命播放 worker，保证连续 enqueue 时不重复创建竞争线程。
- `stop_speaking()` 必须能停止当前朗读、清理未播放队列并最终恢复 idle。
- 保留 wake ack、record done、no speech 等可选提示音能力，但不得再依赖 Overlay。

验收：fake TTS backend 下句子顺序稳定、stop 可中断并清队列、wait 语义确定；Windows 真机下 SAPI 连续朗读无 COM 跨线程错误。对应 `BE-T03`。

### BE-04 统一 ASR 与 STT HTTP 调用链

- 在服务层提供统一 `transcribe_file(path, metadata=None)` 入口。
- 主语音流程、STT HTTP 和外部嵌入调用全部通过该入口访问 FunASR。
- 使用同一个 ASR lock 串行化 SenseVoice 模型调用，修复当前主流程绕过锁的问题。
- 对无效文件、模型加载失败和识别异常保留明确错误，不把失败结果伪装为空字符串。
- 视 `service.py` 复杂度将 STT HTTP server 拆到独立模块，但不为拆文件而改变现有 HTTP 契约。
- 默认继续兼容 `POST http://127.0.0.1:15900/stt` 与 `{"path": "..."}` 请求格式。

验收：主流程和 HTTP 并发识别时不会同时进入 ASR 模型；原 STT client 默认调用仍可用。对应 `BE-T04`。

### BE-05 统一 OpenClaw 文本对话入口

- 实现公开 `ask_text(text, *, speak=True, metadata=None) -> str`。
- 语音识别后的文本和外部直接输入文字都复用同一个 OpenClaw 调用流程。
- `speak=True` 时继续支持 Gateway 流式逐句进入 TTS 队列；`speak=False` 时只返回文字结果，不触发 TTS。
- 正常事件按协议输出：`thinking -> speaking(0..N) -> reply -> idle`；不朗读时为 `thinking -> reply -> idle`。
- 空文本在进入 Gateway 前校验并拒绝。
- 嵌入式公开 API 保留可处理的异常；独立 `run()` 在单轮边界捕获错误、发 `error -> idle` 后继续运行。

验收：不启动麦克风和 wakeword 也能完成一轮 OpenClaw 文本对话；事件顺序、返回值和 `speak=False` 行为与 API 文档一致。对应 `BE-T05`。

### BE-06 外部主动朗读 API

- 实现 `speak_message(text, *, metadata=None, wait=False)`。
- 实现 `stop_speaking()`，复用 `RuntimeControl` 与 `SpeechController`。
- 主动朗读不得依赖 OpenClaw、ASR、wakeword 或 recorder。
- 主动朗读默认发 `speaking -> idle`，不把任意外部文本伪装成 OpenClaw `reply`。
- metadata 原样透传给事件，允许消费方携带 `source`、`message_id` 等上下文。
- 若第一版无法可靠支持“只等待本条消息”，则以明确的全队列等待语义替代，不实现含糊的 wait 行为。

验收：外部应用仅构造核心服务即可主动朗读并停止；多个连续消息按顺序播放且事件不丢失。对应 `BE-T06`。

### BE-07 VoiceControlService 主循环迁移

- 将 wakeword 主循环改为组合公共能力，而不是维护一套独立的 ASR/OpenClaw/TTS 调用链。
- 唤醒后保持现有录音与静音检测能力，录音完成后调用统一 `transcribe_file()`。
- 识别成功后发 `recognized`，再调用统一 `ask_text()`。
- 用 Presenter 事件完全替换 `update_overlay_state()` 的业务用途。
- 修复 prepared recording stream 被重复 `start()` 的问题，明确 wakeword 与录音设备的交接生命周期。
- 优化 wakeword 暂停/恢复逻辑，避免每轮对话无必要重建 openWakeWord 模型。
- 本轮保持当前 `wake -> one turn -> idle` 默认行为，不额外恢复旧 follow-up loop，除非设计文档另行变更。

验收：独立模式下一轮完整语音交互可完成，核心不再通过 Overlay 状态表达 listening/thinking/reply 等业务状态。对应 `BE-T07`。

### BE-08 Gateway WebSocket 与流式回复稳定化

- 保留 `chat.send`、`event.agent` streaming 和 session JSONL fallback 的总体架构。
- 将发送时间戳记录提前到请求发出前，避免 ACK 延迟导致 session 消息匹配遗漏。
- 修复 WS 与 session fallback 共同写入同一 `full_text` 带来的前缀错位和重复朗读风险。
- 建立单一流式文本聚合/去重逻辑，保证一句只 enqueue 一次且顺序稳定。
- 不为每个句子创建新的回调线程；由有序回调或单一队列向 SpeechController 交付。
- 将 ACK、总响应等硬编码超时改为配置驱动。
- 避免全局删除进程中的 proxy 环境变量，仅对目标连接做必要的代理禁用或显式连接配置。
- 修复 WebSocket `close()` 生命周期，确保同步 wrapper 使用的 event loop 能真正关闭连接。
- 将 OpenClaw session 根目录从硬编码盘符抽为可配置路径，并保留合理默认值。

验收：WS-only、session-fallback-only 和两者同时可用的测试场景均不重复朗读、不漏最终回复，close 后连接资源释放。对应 `BE-T08`。

### BE-09 配置、依赖与公开导入面整理

- 删除核心 `OverlayConfig` 及相关 runtime/state file 配置。
- 将 STT HTTP host/port、OpenClaw session key/session root、WS timeout 等真正运行参数配置化。
- 清理 WebSocket 路径已经不用的旧 HTTP model/user 等配置字段；若仍有兼容用途则在文档中明确，不保留无效“看起来可配置”的字段。
- `app.platform` 默认值改为 Windows 目标；移除默认 macOS 音效路径。
- 修正依赖：补齐实际使用的 `websockets`、Windows SAPI 所需依赖，删除核心不再使用的 PySide6、旧 HTTP/Overlay 依赖。
- 保留 `scripts/tts_cli.py` 需要的独立依赖和行为，必要时通过 optional dependency 或 requirements 分组表达。
- 更新包顶层 `__init__.py` 的稳定公共导入面。

验收：fresh install 的依赖与代码实际 import 一致；基础语音核心不安装 PySide6 也能运行。对应 `BE-T09`。

### BE-10 删除 Overlay 与旧平台残留

- 删除 `overlay_app.py`、`state.py`、`run_overlay.bat` 和 Overlay 专用入口。
- 删除 `launchagents/`、macOS host launcher、launchd/install/deploy/restart 等不再属于 Windows 核心架构的脚本。
- 删除仅作为静音 WAV stub 的 `scripts/tts_simple.py`。
- 保留并校准 `scripts/tts_cli.py`、`scripts/stt_endpoint_client.py`、`scripts/list_audio_devices.py`、`scripts/test_microphone.py`。
- 重写 `run_service.bat`，去掉机器特定的 `E:`、`F:` 路径，优先使用仓库 `.venv` 并提供合理 fallback。
- 全仓检查 PySide6、OverlayStateManager、launchctl、macOS 路径和已删除文件引用。

验收：核心源码与启动脚本中不存在 Overlay/PySide6/macOS 运行链引用，保留脚本均有明确用途且可从文档追踪。对应 `BE-T10`。

### BE-11 自动化测试与 Windows CI

- 建立真实 `tests/`，替换仅有 README 的占位结构。
- 覆盖事件协议、Presenter 异常隔离、RuntimeControl、SpeechController 队列、ASR lock、STT HTTP、`ask_text()`、`speak_message()`、Gateway parser/dedupe、配置解析和文本清理。
- 使用 fake ASR、fake Gateway、fake TTS backend 和 RecordingPresenter，避免 CI 下载 SenseVoice 模型或要求真实麦克风/SAPI。
- CI 改为 Windows/Python 3.11 主验证环境，至少执行安装、compile 和 pytest。
- 真实麦克风、openWakeWord、FunASR、SAPI 和 OpenClaw Gateway 作为人工集成测试，不伪装成稳定单元测试。

验收：所有无硬件核心逻辑在 CI 中自动通过；事件顺序和公开 API 回归有测试保护。对应 `BE-T11`。

### BE-12 文档校准与外部消费方集成验证

- 将 `README.md`、`README.zh-CN.md`、架构文档、模块文档、scripts README 和 skill 文档更新为当前 Windows/无 UI 架构。
- 以 `backend-design.md` 和 `api-design.md` 为基准检查实际类名、方法签名、事件字符串、线程语义和错误行为。
- 删除或重写旧 macOS/Overlay 文档，不保留互相冲突的两套“当前架构”。
- 编写最小外部 Presenter 示例，验证消费方只需实现 `Presenter.emit()` 即可接入，不需要引用任何内部 Overlay/Qt 类型。
- 使用一个外部测试 harness 验证 `ask_text()`、`transcribe_file()`、`speak_message()`、`stop_speaking()` 可以独立调用。
- 文档只描述已经实现并验证的现状；暂未实现能力明确标为后续计划。

验收：独立运行和外部嵌入两种模式都完成最小闭环，外部 UI 不需要修改核心代码即可接收事件并控制 TTS。对应 `BE-T12`。

## 3. 推荐顺序

`BE-01 → BE-02 → BE-03 → BE-04 → BE-05 → BE-06 → BE-07 → BE-08 → BE-09 → BE-10 → BE-11 → BE-12`

其中：

- `BE-04` 可在 `BE-03` 的 SpeechController 接口稳定后与部分 Gateway 工作并行推进；
- `BE-08` 的纯解析/去重逻辑可提前编写测试，但 Gateway 主流程接入应在 `BE-05` 的 `ask_text()` 边界稳定后完成；
- `BE-10` 必须在事件、RuntimeControl 和 SpeechController 已完全替代 Overlay 职责后再删除旧实现；
- `BE-11` 不是最后一次性补测试，各 `BE-xx` 应在开发时同步补对应测试，BE-11 负责补齐整体 CI 和缺口；
- `BE-12` 必须在公开 API 与实现基本冻结后执行，避免消费方基于临时接口适配。

## 4. 测试编号映射

- `BE-T01`：事件枚举、VoiceEvent、Null/Console/异常 Presenter、事件记录顺序。
- `BE-T02`：RuntimeControl stop/clear/shutdown 幂等与线程安全。
- `BE-T03`：SpeechController enqueue、顺序、stop、wait、worker 异常和 backend 生命周期。
- `BE-T04`：统一 ASR lock、文件校验、STT HTTP 契约与并发识别。
- `BE-T05`：`ask_text()` speak true/false、事件顺序、完整回复、空输入和 Gateway 错误。
- `BE-T06`：`speak_message()`、metadata、stop、连续播放和等待语义。
- `BE-T07`：wakeword 单轮 orchestrator、录音交接、recognized/idle/error 流程。
- `BE-T08`：Gateway ACK、event.agent、session fallback、流式去重、超时和 close。
- `BE-T09`：配置默认值、环境变量覆盖、依赖导入和公共包导出。
- `BE-T10`：仓库静态扫描，确认 Overlay/PySide6/macOS 残留已清除且保留脚本仍存在。
- `BE-T11`：Windows CI 全量无硬件测试。
- `BE-T12`：独立服务 smoke test + 外部 Presenter/API 集成 smoke test。

## 5. 完成定义

- 核心源码不依赖 PySide6、`overlay_app.py`、`OverlayStateManager` 或文件型 TTS stop flag。
- `VoiceEvent` 与 Presenter 协议稳定，外部应用无需修改核心即可消费 listening/recognized/thinking/reply/speaking/idle/error 事件。
- `transcribe_file()`、`ask_text()`、`speak_message()`、`stop_speaking()` 和 `close()` 行为与 `api-design.md` 一致。
- wakeword、录音、FunASR、STT HTTP、Gateway WebSocket、session fallback 和流式逐句朗读保持可用。
- `scripts/tts_cli.py` 保留并有明确文档，不因本轮 UI 解耦被删除。
- Gateway 流式回复无重复朗读，ASR 主流程与 HTTP 不并发访问同一模型实例。
- Windows CI 中所有 `BE-T01` 至 `BE-T11` 自动测试通过，`BE-T12` 的真实集成 smoke test 有可重复步骤和结果记录。
- 仓库内不再维护旧 macOS/Overlay 作为“当前能力”的脚本或文档。
- 文档与代码一致，不把 follow-up loop、桌宠 UI、OpenClaw 主动推送监听等未在本轮实现的能力描述为现状。
