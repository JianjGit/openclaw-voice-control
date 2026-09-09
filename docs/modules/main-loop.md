# Voice Input Modes and Main Loop

`VoiceControlService.run()` 现在是一个常驻 Voice Core 生命周期。它负责加载并保留 ASR、STT HTTP、Gateway/TTS 相关对象，并根据当前 input mode 决定麦克风由 wakeword 还是 `listen_once()` 驱动。

## `run()` — 常驻 Voice Core

启动时：

1. 加载 ASR；
2. 启动本地 STT HTTP server；
3. 根据当前 input mode 决定是否启动 wakeword 监听；
4. 发 startup `idle`；
5. 保持服务循环，直到 shutdown。

`run()` 不再用一把锁占住整个服务生命周期。输入锁只保护实际麦克风所有权，因此 `push_to_talk` 模式下 `run()` 可以继续常驻，而 `listen_once()` 获得输入锁执行一轮录音。

## `set_input_mode()` — 运行时切换

```python
service.set_input_mode("wakeword")
service.set_input_mode("push_to_talk")
```

模式：

```text
wakeword
  -> wakeword engine listening

push_to_talk
  -> wakeword engine paused
  -> run() remains alive
  -> wait for listen_once()
```

切换不会重建：

- `VoiceControlService`；
- FunASR / SenseVoice；
- SpeechController / Windows TTS；
- OpenClaw Gateway client；
- STT HTTP server。

wakeword 从 `wakeword` 切到 `push_to_talk` 时只调用 `pause()`，保留已经加载的模型/engine；切回来调用 `resume()`。

### 返回值

```python
applied = service.set_input_mode(mode)
```

- `True`：已经应用；
- `False`：当前语音活动未结束，模式已进入 pending，结束后自动应用。

可查询：

```python
service.get_input_mode()
service.get_pending_input_mode()
```

完整说明见 [`../input-modes.md`](../input-modes.md)。

## wakeword 模式流程

wakeword 检测循环：

1. wakeword stream 读取一帧；
2. 命中唤醒词后，当前 turn 原子地标记为 active；
3. `wakeword.pause()`，关闭 wakeword 麦克风但保留模型；
4. 朗读 wake acknowledgement；
5. 使用同一把输入锁切换到 recording stream；
6. `record_until_silence()`；
7. `handle_one_turn()` 组合 `transcribe_file()` 和 `ask_text()`；
8. 等 ASR / Gateway / TTS 本轮全部结束；
9. 如果模式仍是 `wakeword`，`wakeword.resume()`；
10. 如果 pending 是 `push_to_talk`，保持 wakeword paused 并在本轮结束后应用模式。

因此 wakeword 不会在当前回答仍朗读时被提前恢复。

## `listen_once()` — push-to-talk 输入

当常驻服务已经切到：

```text
push_to_talk
```

桌宠/bridge 可以调用：

```python
ok = service.listen_once(
    speak=True,
    metadata={"source": "desktop_pet"},
)
```

流程：

```text
listen_once()
  -> acquire shared microphone input lock
  -> record_until_silence()
  -> transcribe_file()
  -> recognized
  -> ask_text()
  -> streaming TTS when speak=True
  -> reply
  -> idle
  -> release input lock
```

如果 `run()` 正在运行且当前仍为 `wakeword`，直接调用 `listen_once()` 会抛：

```python
RuntimeError("listen_once requires push_to_talk input mode while run() is active")
```

这避免桌宠忘记切模式后又打开第二个 microphone stream。

如果没有启动 `run()`，`listen_once()` 仍保留原先的一次性 SDK 使用方式。

## 模式切换安全边界

Voice Core 对 recording、ASR、Gateway conversation 和 speech activity 做活动计数。

当这些活动存在时：

```text
set_input_mode(new_mode)
  -> pending_input_mode = new_mode
  -> return False
  -> current turn continues normally
  -> activity count reaches zero
  -> apply pending mode
```

模式真正生效后会发：

```text
idle
metadata.source = input_mode
metadata.input_mode = wakeword | push_to_talk
metadata.mode_change = applied
```

Presenter/bridge 可以利用这个事件确认 deferred switch 最终完成。

## 单一麦克风锁

wakeword read/turn 与 `listen_once()` 共用 `_input_mode_lock`。

切到 `push_to_talk` 时，必须先获得这把锁并完成 `wakeword.pause()`，然后才把模式标记为已生效；切回 `wakeword` 时也要先确认 `listen_once()` 已释放同一把锁，再 resume wakeword。

因此两条路径不会同时打开麦克风。

## Shutdown

`RuntimeControl.request_shutdown()` 终止常驻循环。`run()` 最终调用幂等 `close()`，此时才真正释放 wakeword model、SpeechController/TTS、STT server 和 Gateway client。
