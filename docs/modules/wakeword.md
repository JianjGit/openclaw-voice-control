# 唤醒词引擎流程

> 源码：`src/openclaw_voice_control/wakeword.py`

---

## OpenWakeWord (默认)

```
1. [@OpenclawVoiceControl] start()
  1.1 下载/加载 "hey jarvis" 模型 (ONNX/TFLite)
  1.2 开启麦克风输入流 (16000Hz)

2. read() → (pcm, keyword_index)
  2.1 读取 1280 采样帧
  2.2 模型推理 → 置信度分数
  2.3 IF 分数 >= 阈值 (0.35): keyword_index = 0 (触发)
    2.3.1 [@User] 说出唤醒词触发
  2.4 ELSE: keyword_index = -1
```

## Porcupine (备选)

```
3. 使用 PvRecorder + pvporcupine 替代，需 PICOVOICE_ACCESS_KEY
```

## 工厂函数

```
4. build_wakeword_engine({config}) → 根据 provider 选择引擎
```
