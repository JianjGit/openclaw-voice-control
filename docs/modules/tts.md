# TTS 语音播报流程

> 源码：`src/openclaw_voice_control/tts.py` — `WindowsTTS`

---

## 初始化

```
1. [@OpenclawVoiceControl] 通过 COM 接口获取 SAPI.SpVoice 实例
2. 设置语速 Rate=1, 音量 Volume=100, 匹配语音 zh-CN-HUIHUI
```

## 播报

```
3. [@OpenclawVoiceControl] speak({text}) → bool
  3.1 文本清理：去 Markdown/emoji，换行→句号
  3.2 停止当前正在播放的语音 (Skip + Speak(""))
  3.3 按句号/感叹号/问号正则分句 → {sentences}
  3.4 FOR {sentence} IN {sentences}:
    3.4.1 IF 用户请求停止: return False
    3.4.2 同步播放 {sentence}
  3.5 return True
```

## 流式队列播放（核心改进）

```
4. [@OpenclawVoiceControl] enqueue({sentence}) — 非阻塞
  4.1 文本清理后推入 {_sentence_queue}
  4.2 IF 播放线程未运行: 启动 _play_queue() 后台线程

5. _play_queue() — 单线程顺序播放
  5.1 LOOP:
    5.1.1 IF 停止请求: 清空队列 → BREAK
    5.1.2 从队列取句 (0.3s 超时)
    5.1.3 IF 队列空: BREAK
    5.1.4 Speak({sentence}, 0)  # 同步阻塞
  5.2 设置 {_player_ready} 完成信号

6. wait_done() — 等待队列全部播完 (最长 30s)
```

## 停止机制

```
7. 两层停止标志：
  7.1 文件标志: runtime/stop_tts.flag（跨进程，Overlay UI 写入）
  7.2 Event 标志: threading.Event（进程内）
  7.3 队列玩家检测到停止后清空队列并退出
```
