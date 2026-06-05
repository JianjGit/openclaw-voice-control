# 录音流程

> 源码：`src/openclaw_voice_control/service.py` — `record_until_silence()`

---

```
1. [@OpenclawVoiceControl] 创建音频输入流 (InputStream, 16000Hz, mono)
2. LOOP 每 0.1s 取一帧 (最长 60s):
  2.1 计算 RMS 能量
  2.2 IF 用户还没开始说话:
    2.2.1 连续 3 帧 RMS 超过阈值 → 判定开始说话，回溯保留之前 10 帧
    2.2.2 超过 3s 没检测到语音 → 超时返回 None
  2.3 ELSE: # 正在说话中
    2.3.1 RMS 低于阈值 → 累计静音时间
    2.3.2 连续静音 1.2s → 判定说话结束，BREAK
3. IF 说话时长 < 0.4s: 返回 None (太短，无效)
4. 将所有语音帧写入临时 WAV 文件 → 返回路径
```
