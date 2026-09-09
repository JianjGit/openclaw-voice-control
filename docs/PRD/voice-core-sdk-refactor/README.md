# Voice Core SDK Refactor

本目录记录 `openclaw-voice-control` 本轮重构的产品与技术设计。

本轮目标不是为某一个具体桌宠项目定制 UI，而是把仓库重构为一个可独立运行、也可作为其他桌面应用依赖使用的无界面语音核心。核心仓库只提供语音流程、OpenClaw 通信、TTS/STT 能力、事件协议和扩展点；具体立绘、气泡、动画与桌宠状态映射全部由消费方仓库实现。

## 文档

- [`01-functional-prd.md`](./01-functional-prd.md)：功能 PRD、范围、兼容性、验收标准。
- [`02-backend-design.md`](./02-backend-design.md)：模块拆分、运行时控制、事件流、线程模型和迁移方案。
- [`03-public-api.md`](./03-public-api.md)：拟公开的 Python API、事件协议、Presenter 接口和使用约定。

## 核心原则

1. 核心不依赖 PySide6，不包含桌宠 UI。
2. `service.py` 不直接写悬浮窗状态，所有展示需求通过标准事件输出。
3. 默认使用 `NullPresenter`，保证独立运行时不需要图形界面。
4. 唤醒词、录音、FunASR、STT HTTP、Gateway、流式逐句 TTS 能力保持兼容。
5. TTS 停止与队列控制从 Overlay 状态文件中解耦。
6. 对外提供文本对话、语音转文字、主动朗读等稳定入口。
7. `scripts/tts_cli.py` 在本轮保留，并作为独立 TTS 文件生成工具继续维护。
8. 桌宠仓库只实现自己的 Presenter 与 UI 映射，不把具体桌宠逻辑写回本仓库。

## 当前状态

该目录描述的是目标设计，代码尚需按这些文档逐步迁移。若实现与文档发生冲突，应优先更新文档并在变更中说明原因，避免重新出现“代码与架构文档各说一套”的情况。
