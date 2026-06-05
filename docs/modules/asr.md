# ASR 语音识别流程

> 源码：`src/openclaw_voice_control/asr.py` — `FunASRSenseVoice`

---

## 模型加载

```
1. [@OpenclawVoiceControl] load()
  1.1 将 FFMPEG_PATH 加入 PATH
  1.2 用 AutoModel 加载 SenseVoiceSmall + fsmn-vad (首次耗时长)
```

## 识别

```
2. [@OpenclawVoiceControl] transcribe({wav_path}) → str
  2.1 确保模型已加载
  2.2 调用 generate(): WAV → VAD 分段 → 逐段识别 → ITN 后处理
  2.3 返回清理后的文本
```

## 并发保护

```
3. 主录音线程与 STT HTTP 线程共享 {asr_lock}，互斥调用 transcribe()
```

## STT HTTP 接口

```
4. [@OpenclawVoiceControl] 守护线程监听 127.0.0.1:15900
  4.1 POST /stt + {"path": "..."} → 返回 {"text": "识别结果"}
```
