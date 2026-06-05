# OpenClaw Voice Control — 架构文档 v2

> 源码：`F:\script\openclaw-voice-control\`  
> 版本：HEAD (08292d1) + 本地未提交修改  
> 平台：Windows 10, Python 3.11

---

## 项目概览

本地语音控制服务，通过唤醒词触发 → 录音 → 语音识别 → OpenClaw Gateway → TTS 播报，实现免提 AI 对话。支持追问循环和 STT HTTP 接口供外部调用。

---

## 项目文件夹结构

```
openclaw-voice-control/
├── config/
│   └── default.yaml              # 默认配置
├── docs/                          # 文档
├── src/openclaw_voice_control/
│   ├── __init__.py               # v0.1.0
│   ├── __main__.py               # python -m 入口
│   ├── cli.py                    # CLI 解析 + 启动
│   ├── config.py                 # YAML + .env 配置加载
│   ├── service.py                # ★ 主控循环
│   ├── asr.py                    # FunASR SenseVoice 语音识别
│   ├── tts.py                    # Windows SAPI5 语音合成
│   ├── gateway_ws.py             # Gateway WebSocket 通信
│   ├── openclaw_client.py        # Gateway 客户端封装
│   ├── state.py                  # 状态文件管理
│   ├── text.py                   # 文本清理 (Markdown/Emoji)
│   ├── wakeword.py               # 唤醒词引擎
│   └── overlay_app.py            # PySide6 悬浮窗 UI
├── .env / .env.example           # 环境变量
├── pyproject.toml                # 项目元数据
├── requirements.txt              # pip 依赖
├── run_service.bat               # Windows 启动脚本
├── run_overlay.bat               # Overlay 启动脚本
├── assets/wakeword/               # 唤醒词模型
├── models/                       # ASR 离线模型
├── logs/                         # 日志输出
├── runtime/                      # 运行时状态文件
└── skills/                       # OpenClaw Skill 定义
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 语音识别 (ASR) | FunASR + SenseVoiceSmall + fsmn-vad |
| 语音合成 (TTS) | Windows SAPI5 (COM) |
| 唤醒词检测 | openWakeWord (默认) / Porcupine (备选) |
| 音频采集 | sounddevice (PortAudio) |
| Gateway 通信 | WebSocket (websockets 库) |
| UI 悬浮窗 | PySide6 |
| 深度学习推理 | PyTorch + torchaudio |
| 配置 | YAML + .env 环境变量 |

---

## 总体流程

```
1. [@OpenclawVoiceControl] 解析命令行参数，加载配置，初始化各模块
2. 进入主循环:
  2.1 持续监听唤醒词
  2.2 IF [@User] 说唤醒词:
    2.2.1 [@OpenclawVoiceControl] 播报唤醒确认 "我在"
    2.2.2 [@User] 听到播报，开始说话
    2.2.3 [@OpenclawVoiceControl] 关闭唤醒词引擎 → 录音直到静音 → {wav_path}
    2.2.4 [@OpenclawVoiceControl] 同步调用 [对话处理流程] # 阻塞等待完成
    2.2.5 重新启动唤醒词引擎，回到 idle
```

### 对话处理流程

```
# 对话处理流程 (同步函数: handle_one_turn)
1. [@OpenclawVoiceControl] 语音转文字: {wav_path} → {user_text}
2. IF {user_text} 为空: return
3. [@OpenclawVoiceControl] 通过 WebSocket 连接发送 {user_text} 到 [@OpenclawGateway]
  3.1 消息添加 🎤 前缀标记，用于后续 session 文件匹配
  3.2 [@OpenclawGateway] 在 dashboard 上显示 {user_text}
  3.3 记录 send_timestamp = time.time()
4. [@OpenclawGateway] 发送 {user_text} 到 [@AiApiBackend]
5. [@AiApiBackend] 流式生成回复，[@OpenclawGateway] 在 dashboard 上流式显示
6. [@OpenclawGateway] 通过 WebSocket 流式返回回复文本 ([@OpenclawVoiceControl] event.agent 接收)
7. [@OpenclawVoiceControl] 双路径轮询获取回复:
  7.1 路径A: WebSocket event.agent 事件 (实时流式)
  7.2 路径B: Session 文件轮询 (完整回复)
    7.2.1 排除 .trajectory.jsonl 文件
    7.2.2 时间戳匹配: 只查找 msg_unix >= send_timestamp - 1 的消息
8. [@OpenclawVoiceControl] 逐句推入 TTS 播放队列（单线程顺序播放，不互相打断）：
  8.1 FOR {sentence} IN 流式回复:
    8.1.1 推入队列 → 后台播放线程顺序 Speak
    8.1.2 [@User] 收听播报
9. [@OpenclawVoiceControl] 等待队列播完，在 overlay 上显示完整回复文本
```

---

## 模块简介

### CLI & 配置

- 简介：命令行入口，参数解析，YAML + .env 配置加载，配置优先级处理
- 文件：`src/openclaw_voice_control/cli.py`, `config.py`
- 文档路径：`docs/modules/cli-and-config.md`

### 主控循环

- 简介：VoiceControlService 主控循环，编排唤醒→录音→ASR→Gateway→TTS→追问的完整对话生命周期
- 文件：`src/openclaw_voice_control/service.py`
- 文档路径：`docs/modules/main-loop.md`

### 录音

- 简介：麦克风录音，RMS 能量检测实现语音活动检测(VAD)，判定说话开始/结束，输出 WAV 文件
- 文件：`src/openclaw_voice_control/service.py` — `record_until_silence()`
- 文档路径：`docs/modules/record.md`

### ASR — 语音识别

- 简介：FunASR SenseVoiceSmall 语音→文字，含 STT HTTP 接口（端口 15900）供外部 CLI 调用
- 文件：`src/openclaw_voice_control/asr.py`
- 文档路径：`docs/modules/asr.md`

### TTS — 语音播报

- 简介：Windows SAPI5 COM 文字→语音，分句同步播放，停止机制（文件标志 + Event），流式逐句播报
- 文件：`src/openclaw_voice_control/tts.py`
- 文档路径：`docs/modules/tts.md`

### Gateway 通信

- 简介：WebSocket 连接 OpenClaw Gateway，流式发送消息并双路径（WS事件 + Session文件）轮询接收回复
- 文件：`src/openclaw_voice_control/gateway_ws.py`, `openclaw_client.py`
- 文档路径：`docs/modules/gateway-ws.md`

### 唤醒词

- 简介：openWakeWord (默认) / Porcupine (备选) 唤醒词检测引擎
- 文件：`src/openclaw_voice_control/wakeword.py`
- 文档路径：`docs/modules/wakeword.md`

### 状态 & 文本

- 简介：JSON IPC 状态管理（Overlay 跨进程通信），停止标志文件协调，Markdown/Emoji 文本清理
- 文件：`src/openclaw_voice_control/state.py`, `text.py`
- 文档路径：`docs/modules/state-and-text.md`

---

## 参考文档

```
docs/
├── architecture.md               # 旧版架构（macOS 原始版）
├── architecture-v2.md            # 本文档（Windows 适配版概览）
├── modules/                      # 各模块详细流程文档
│   ├── cli-and-config.md
│   ├── main-loop.md
│   ├── record.md
│   ├── asr.md
│   ├── tts.md
│   ├── gateway-ws.md
│   ├── wakeword.md
│   └── state-and-text.md
├── fresh-clone-validation.md
├── macos-install.md
├── release-checklist.md
└── same-machine-test.md
```
