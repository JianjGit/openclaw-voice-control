# Voice Core SDK Refactor

本目录记录 `openclaw-voice-control` 在 2026-09-09 这轮 Voice Core SDK 重构中的产品设计、技术设计、API 契约与实施记录。

本轮目标不是为某一个具体桌宠项目定制 UI，而是把仓库重构为一个可独立运行、也可作为其他桌面应用依赖使用的无界面 Windows 语音核心。核心仓库只提供语音流程、OpenClaw 通信、TTS/STT、事件协议和扩展点；具体立绘、气泡、动画与桌宠状态映射由消费方仓库实现。

## 文档

- [`functional-design.md`](./functional-design.md)：功能设计、范围、兼容性与验收标准。
- [`backend-design.md`](./backend-design.md)：模块拆分、运行时控制、事件流、线程模型和迁移方案。
- [`backend-dev-plan.md`](./backend-dev-plan.md)：BE-01～BE-12 实施任务、测试编号和完成状态。
- [`api-design.md`](./api-design.md)：公开 Python API、事件协议、Presenter 接口和使用约定。

## 已落地原则

1. 核心不依赖 PySide6，也不包含桌宠/Overlay UI。
2. `service.py` 通过 `VoiceEvent` / Presenter 输出业务状态，不写 UI 状态文件。
3. 默认 `NullPresenter`，外部 GUI 可通过自己的 Presenter 接入。
4. 唤醒词、录音、FunASR、STT HTTP、Gateway WebSocket + session fallback、逐句 TTS 能力保留。
5. TTS stop/shutdown 使用 `RuntimeControl`，不依赖文件型 flag。
6. 对外提供 `transcribe_file()`、`ask_text()`、`speak_message()`、`stop_speaking()` 和幂等 `close()`。
7. `scripts/tts_cli.py` 作为独立文件合成工具保留。
8. 默认目标平台和 CI 目标均为 Windows/Python 3.11。

## 当前状态

BE-01～BE-12 的代码、测试入口和文档迁移已经在 `docs/voice-core-sdk-refactor` 分支落地。`backend-dev-plan.md` 记录逐项实现状态与验证边界。

当前使用说明以仓库根目录 `README.md` / `README.zh-CN.md` 和 `docs/architecture.md` 为准；本目录保留重构期间的设计背景与迁移决策。设计文档中用于说明“旧实现 → 新实现”的 Overlay/macOS 内容属于历史迁移上下文，不代表当前运行架构。

真实麦克风、SenseVoice 模型、openWakeWord、Windows SAPI 出声和 live OpenClaw Gateway 仍属于机器级集成验证，步骤见 `docs/same-machine-test.md`。
