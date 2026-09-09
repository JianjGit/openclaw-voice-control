# Voice Core SDK 后端开发计划

## 0. 实施状态

- [x] BE-01 事件模型与 Presenter 协议 — `BE-T01` 覆盖事件枚举、默认 Presenter、异常隔离与事件记录，提交 `1dd11b8`。
- [x] BE-02 RuntimeControl 与停止机制解耦 — `BE-T02` 覆盖 stop/clear/shutdown 幂等与线程安全，提交 `4d54bc1`。
- [x] BE-03 SpeechController 与 Windows TTS 重构 — `BE-T03` fake backend 覆盖 FIFO、stop、wait、worker 异常和 backend 生命周期，提交 `6cfef1f`；真实 SAPI 出声属于机器级验证。
- [x] BE-04 统一 ASR 与 STT HTTP 调用链 — `BE-T04` 覆盖统一 ASR lock、文件校验、HTTP 契约与并发识别，提交 `78d4bdd`。
- [x] BE-05 统一 OpenClaw 文本对话入口 — `BE-T05` 覆盖事件顺序、`speak=False`、空输入、错误与 turn lock，提交 `c06f740`。
- [x] BE-06 外部主动朗读 API — `BE-T06` 覆盖 FIFO、单条 wait、stop、错误与 metadata，提交 `4d070b4`。
- [x] BE-07 VoiceControlService 主循环迁移 — `BE-T07` 覆盖单轮编排、录音交接、Presenter 事件和 wakeword pause/resume，提交 `e764bae`。
- [x] BE-08 Gateway WebSocket 与流式回复稳定化 — `BE-T08` 覆盖 WS/session 聚合去重、ACK 早到事件、配置超时与 close 生命周期，提交 `b366b67`。
- [x] BE-09 配置、依赖与公开导入面整理 — `BE-T09` 覆盖 Windows/headless 默认值、环境变量覆盖、依赖元数据与公共导入；核心依赖与 Porcupine / `tts_cli` optional extras 已分离。
- [x] BE-10 删除 Overlay 与旧平台残留 — `BE-T10` 静态回归锁定已删除路径、保留 Windows 工具并禁止核心/启动器重新引入 PySide6、Overlay、macOS 运行链；`run_service.bat` 已去机器盘符。
- [x] BE-11 自动化测试与 Windows CI — `BE-T01`～`BE-T12` 已形成真实 `tests/`；`.github/workflows/ci.yml` 已改为 `windows-latest` / Python 3.11，执行 editable install、`pip check`、`compileall`、`pytest`。当前仓库 Actions API 仍返回 0 条 workflow run，因此不能声称远端 CI 已实际跑绿。
- [x] BE-12 文档校准与外部消费方集成验证 — README、架构、模块、scripts、examples、中英文 skill 已统一到 Windows/headless Voice Core；`examples/external_presenter.py` 与 `BE-T12` fake harness 覆盖 Presenter、`transcribe_file()`、`ask_text()`、`speak_message()`、`stop_speaking()`、幂等 `close()`。真实麦克风、SenseVoice、wakeword、SAPI 和 live Gateway 验证步骤保留在 `docs/same-machine-test.md`，需在目标 Windows 机器执行。

## 1. 实施原则

- 以 `functional-design.md`、`backend-design.md` 和 `api-design.md` 为契约，不把具体桌宠 UI、立绘、气泡、动画或状态映射写入核心仓库。
- 公共能力、事件协议、运行时控制先稳定，再由独立 wakeword 主循环组合这些能力。
- 所有 SenseVoice 调用共用一个 ASR lock；所有对话轮次共用一个 turn lock。
- SpeechController 使用单一长寿命 FIFO worker；Windows SAPI COM 对象只在其所属 worker 线程创建、使用和销毁。
- Gateway WebSocket 与 session JSONL fallback 共享单一文本聚合/去重逻辑，不为每个句子创建回调线程。
- `scripts/tts_cli.py` 作为独立文件合成工具保留，不与实时 SpeechController 混为同一实现。
- 硬件、模型和真实 OpenClaw 连接不伪装成稳定 CI；无硬件逻辑使用 fake/mock 自动化覆盖，真实链路使用机器级步骤验证。

## 2. 开发任务与最终实现

### BE-01 事件模型与 Presenter 协议

实现稳定的 `VoiceEventKind` / `VoiceEvent`，事件值固定为 `listening`、`recognized`、`thinking`、`reply`、`speaking`、`idle`、`error`。提供 `Presenter.emit(event)` 协议、`NullPresenter` 与 `ConsolePresenter`；服务统一通过 `_emit()` 隔离 Presenter 异常。公共类型从包顶层导出。

### BE-02 RuntimeControl 与停止机制解耦

使用 `threading.Event` 管理停止朗读和 shutdown，不再依赖 Overlay 状态文件或 `stop_tts.flag`。外部 stop/clear/shutdown 操作幂等且可跨线程调用。

### BE-03 SpeechController 与 Windows TTS

SpeechController 管理 FIFO 队列、单 worker、停止、等待与完成回调。Windows SAPI5 backend 的 COM 生命周期全部位于 speech worker。停止当前朗读时清理待播放队列并恢复 idle。真实 SAPI 音频输出作为 Windows 真机检查。

### BE-04 统一 ASR 与 STT HTTP

公开 `transcribe_file(path, metadata=None)`；wakeword 单轮、嵌入调用和 `POST /stt` 全部走同一入口和同一 ASR lock。无效路径、模型加载和识别错误保持明确异常。STT host/port 已配置化，默认仍为 `127.0.0.1:15900`。

### BE-05 统一文本对话入口

公开 `ask_text(text, *, speak=True, metadata=None) -> str`。`speak=True` 使用 Gateway streaming 并逐句进入 SpeechController；`speak=False` 只返回文字。正常事件为 `thinking -> speaking* -> reply -> idle` 或 `thinking -> reply -> idle`。Gateway 错误发 `error -> idle`，公共 API 保留异常给调用方处理。

### BE-06 外部主动朗读

公开 `speak_message(text, *, metadata=None, wait=False)` 与 `stop_speaking()`。主动朗读不依赖 OpenClaw、ASR、wakeword 或 recorder；默认事件为 `speaking -> idle`，metadata 原样透传。`wait=True` 等待该入队 item 完成。

### BE-07 独立 VoiceControlService 主循环

独立模式只负责组合公共能力：wakeword -> wake ack -> pause wakeword audio -> 单次启动 prepared record stream -> `record_until_silence()` -> `transcribe_file()` -> `recognized` -> `ask_text()` -> idle -> resume wakeword。当前默认是一唤醒一轮，不实现 follow-up loop。业务状态完全通过 Presenter 事件表达。

### BE-08 Gateway WebSocket 稳定化

保留 `chat.send`、`event.agent` 与 session JSONL fallback。请求发送前记录时间戳；WS snapshot 与 session snapshot 只能推进同一个 accumulator；完整句子按顺序同步交给上层，避免重复朗读。ACK / 总响应超时配置化；`OPENCLAW_HOME` 配置 session 根目录；代理设置只作用于 websocket 连接；close 关闭 websocket 和同步 wrapper event loop。

### BE-09 配置、依赖与公共导入

删除 `OverlayConfig`、runtime/state file 配置和无效旧 HTTP model/user 配置。新增/整理 OpenClaw WS、session、home、timeout 与 STT host/port 参数。默认平台为 Windows，提示音默认路径为空。主依赖只保留核心实际运行依赖；Porcupine 使用 `[porcupine]` extra，`scripts/tts_cli.py` 使用 `[tts-cli]` extra。基础核心不依赖 PySide6。

### BE-10 删除 Overlay 与旧平台残留

删除 `overlay_app.py`、`state.py`、`run_overlay.bat`、`launchagents/`、macOS host/install/deploy/restart/uninstall 链以及 `tts_simple.py`。保留并校准 `tts_cli.py`、`stt_endpoint_client.py`、`list_audio_devices.py`、`test_microphone.py`。`run_service.bat` 优先仓库 `.venv`，否则使用 PATH 中的 Python，不含机器特定 `E:` / `F:` 路径。

### BE-11 自动化测试与 Windows CI

`tests/` 覆盖事件协议、RuntimeControl、SpeechController、ASR lock、STT HTTP、`ask_text()`、`speak_message()`、主循环编排、Gateway parser/dedupe、配置/依赖、仓库清理、文本清理和外部集成 smoke。CI 目标为 Windows/Python 3.11，步骤包括安装、`pip check`、compile 和 pytest。真实麦克风、模型、SAPI 与 live Gateway 明确排除在稳定单元测试之外。

### BE-12 文档与外部消费方集成

根 README、中英文说明、当前架构、模块文档、scripts README、fresh-clone/same-machine/release 文档和中英文 skill 均已校准为 Windows/headless 现状。删除冲突的第二套旧架构文档，状态文件模块文档替换为事件/RuntimeControl/文本清理文档。外部 Presenter 示例只依赖公共事件契约，不引用 Qt/Overlay 内部类型。

## 3. 测试编号映射

- `BE-T01`：事件枚举、VoiceEvent、Null/Console/异常 Presenter、事件记录。
- `BE-T02`：RuntimeControl stop/clear/shutdown 幂等与线程安全。
- `BE-T03`：SpeechController FIFO、stop、wait、worker 异常和 backend 生命周期。
- `BE-T04`：统一 ASR lock、文件校验、STT HTTP 与并发识别。
- `BE-T05`：`ask_text()` speak true/false、事件顺序、完整回复、空输入、Gateway 错误与 turn lock。
- `BE-T06`：`speak_message()`、metadata、stop、连续播放、等待与 backend 错误。
- `BE-T07`：wakeword 单轮 orchestrator、录音交接、recognized/idle/error 与 pause/resume。
- `BE-T08`：Gateway ACK、event.agent、session fallback、流式去重、超时和 close。
- `BE-T09`：配置默认值、环境变量、依赖/optional extras 和公共包导出。
- `BE-T10`：仓库静态扫描，确认旧运行链已删除且指定 Windows 工具保留。
- `BE-T11`：共享文本清理 + Windows CI 全量无硬件测试入口。
- `BE-T12`：结构化外部 Presenter + 公共 API/fake backend smoke + 幂等 close。

## 4. 完成定义

代码与仓库结构层面的完成条件已经落地：

- 核心源码不依赖 PySide6、`overlay_app.py`、`OverlayStateManager` 或文件型 TTS stop flag。
- `VoiceEvent` / Presenter 协议稳定，外部应用无需修改核心即可消费七类基础事件。
- `transcribe_file()`、`ask_text()`、`speak_message()`、`stop_speaking()`、`run()` 和 `close()` 与 API 设计对齐。
- wakeword、录音、FunASR、STT HTTP、Gateway WebSocket、session fallback 和流式逐句朗读仍保留在当前架构中。
- Gateway 流式回复使用单一聚合/去重游标，ASR 主流程与 HTTP 不会并发进入同一模型实例。
- 旧 macOS/Overlay 运行入口已从当前仓库结构删除；当前使用文档不再把它们描述为现状。
- follow-up loop、桌宠 UI、角色素材和 OpenClaw 主动推送监听均明确不属于本轮现状。

## 5. 外部验证边界

本轮实现完成不等于已经在当前 ChatGPT 工具环境中完成真实硬件验收。以下项目必须在目标 Windows 机器执行：

1. fresh install / `pip check` / `pytest` 的真实 GitHub Actions 或本机结果；
2. 麦克风可见性与录音；
3. openWakeWord / Porcupine 实际检测；
4. SenseVoice + VAD 实际模型推理；
5. Windows SAPI5 连续朗读与 stop；
6. live OpenClaw Gateway + session fallback 完整回复、无重复朗读；
7. STT HTTP 真实音频文件调用；
8. 服务 shutdown 后麦克风、STT port、wakeword、speech worker 和 Gateway 资源释放。

当前 GitHub Actions API 对 `docs/voice-core-sdk-refactor` 返回 0 条 workflow run，因此仓库中已配置 Windows CI，但远端“CI 已跑绿”尚无可引用的运行记录。真实机器步骤见 `docs/fresh-clone-validation.md`、`docs/same-machine-test.md` 和 `docs/release-checklist.md`。
