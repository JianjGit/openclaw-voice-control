# 主控循环流程

> 源码：`src/openclaw_voice_control/service.py` — `VoiceControlService`

---

## 启动

```
1. [@OpenclawVoiceControl] 初始化各模块（ASR模型、唤醒词、TTS、STT HTTP、Overlay状态）
2. 加载 SenseVoiceSmall 模型
3. 启动 STT HTTP 守护线程 (127.0.0.1:15900)
4. 启动唤醒词引擎，进入主循环
```

## 主循环

```
5. [@OpenclawVoiceControl] LOOP 无限:
  5.1 轮询唤醒词引擎
  5.2 IF [@User] 说唤醒词 AND 不在冷却期:
    5.2.1 播报 "我在"
    5.2.2 IF 播报被中断: 回到循环
    5.2.3 关闭唤醒词引擎，释放麦克风
    5.2.4 [@User] 开始说话
    5.2.5 调用 [录音流程] → {wav_path}
    5.2.6 同步调用 [对话处理流程]({wav_path}) # 阻塞等待完成
    5.2.7 重启唤醒词引擎，回到 idle
```

## 对话处理流程 (同步函数: handle_one_turn)

```
1. [@OpenclawVoiceControl] 语音转文字: {wav_path} → {user_text}
2. IF 为空: 提示 "没有识别出有效文本" → return
3. 通过 WebSocket 发送 {user_text} 到 [@OpenclawGateway]
  3.1 [@OpenclawGateway] 在 dashboard 上显示 {user_text}
4. [@OpenclawGateway] 发送 {user_text} 到 [@AiApiBackend]
5. [@AiApiBackend] 流式生成回复，[@OpenclawGateway] 在 dashboard 上流式显示
6. [@OpenclawGateway] 通过 WebSocket 流式返回回复文本
7. [@OpenclawVoiceControl] 逐句推入 TTS 单线程播放队列（不互相打断）：
  7.1 [@User] 收听播报
8. [@OpenclawVoiceControl] 等待队列播完，在 overlay 上显示完整回复文本
```
