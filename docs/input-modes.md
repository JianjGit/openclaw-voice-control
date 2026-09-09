# 运行时输入模式切换

`VoiceControlService` 支持在**同一个常驻服务实例**中切换两种麦克风输入模式，不需要重启桌宠、bridge、Voice Core 进程，也不会重新创建 ASR、TTS、OpenClaw Gateway 或 `VoiceControlService`。

## 1. 两种模式

### `wakeword`

```python
service.set_input_mode("wakeword")
```

行为：

```text
VoiceControlService.run() 常驻
  -> wakeword engine resume / start
  -> 持续监听唤醒词
  -> 唤醒后进入一轮 recording -> ASR -> Gateway -> TTS
  -> 本轮结束
  -> 再次恢复 wakeword 监听
```

### `push_to_talk`

```python
service.set_input_mode("push_to_talk")
```

行为：

```text
VoiceControlService.run() 仍然常驻
  -> wakeword engine pause
  -> wakeword 模型保留在内存中
  -> ASR / TTS / Gateway / STT server / service 实例继续存在
  -> 等待桌宠或 bridge 调用 listen_once()
```

用户点击麦克风按钮时：

```python
service.listen_once(
    speak=True,
    metadata={"source": "desktop_pet"},
)
```

然后走：

```text
listen_once
  -> recording
  -> SenseVoice / FunASR
  -> recognized
  -> OpenClaw Gateway
  -> streaming TTS（speak=True）
  -> reply
  -> idle
```

## 2. 推荐生命周期

桌宠/bridge 启动时只创建一次 Voice Core：

```python
import threading

from openclaw_voice_control import VoiceControlService
from openclaw_voice_control.config import load_config

service = VoiceControlService(load_config("config/default.yaml", ".env"), presenter=presenter)

voice_thread = threading.Thread(
    target=service.run,
    name="voice-core",
    daemon=True,
)
voice_thread.start()
```

之后只切模式：

```python
service.set_input_mode("wakeword")
service.set_input_mode("push_to_talk")
```

不要为了切换模式重新构造 `VoiceControlService`，也不要重启 Python 进程。

## 3. `set_input_mode()` 返回值

签名：

```python
def set_input_mode(self, mode: str) -> bool:
    ...
```

合法值：

```text
wakeword
push_to_talk
```

返回：

- `True`：切换已经应用；
- `False`：切换请求已经登记为 pending，当前语音活动结束后自动应用。

例如 bridge 可以直接这样处理：

```python
applied = service.set_input_mode("push_to_talk")

if applied:
    send_to_pet({
        "type": "voice_input_mode",
        "status": "applied",
        "mode": service.get_input_mode(),
    })
else:
    send_to_pet({
        "type": "voice_input_mode",
        "status": "pending",
        "mode": service.get_input_mode(),
        "pending_mode": service.get_pending_input_mode(),
        "message": "当前对话结束后切换",
    })
```

可查询：

```python
service.get_input_mode()          # 当前已经生效的模式
service.get_pending_input_mode()  # 等待生效的模式；没有则为 None
```

## 4. 安全规则：当前轮次中不强切

以下活动进行中时，`set_input_mode()` 不会立即 pause/resume 输入链路：

- 正在录音；
- 正在 ASR；
- 正在 OpenClaw 对话；
- 正在 streaming TTS / 主动朗读。

此时新模式保存为 pending：

```text
current turn active
  + set_input_mode("push_to_talk")
          |
          v
pending_input_mode = push_to_talk
          |
          v
当前轮 recording / ASR / Gateway / TTS 正常完成
          |
          v
pause wakeword
          |
          v
mode = push_to_talk
```

不会为了模式切换中断当前回答，也不会在 ASR/TTS 中间关闭相关对象。

如果用户在 pending 期间又切回当前模式，例如：

```text
current = wakeword
pending = push_to_talk
用户再次选择 wakeword
```

则 pending 切换会被取消。

## 5. 安全规则：wakeword 与 `listen_once()` 共用同一把输入锁

Voice Core 内部只有一把麦克风所有权锁。

两条输入路径都必须经过它：

```text
                 +-----------------------+
                 | microphone input lock |
                 +-----------+-----------+
                             |
             +---------------+---------------+
             |                               |
             v                               v
      wakeword read/turn                 listen_once()
```

因此永远不会出现：

```text
wakeword InputStream 打开
+
listen_once recording InputStream 同时打开
```

### wakeword -> push_to_talk

切换完成顺序：

```text
等待当前 wakeword read frame / 当前轮结束
  -> 获得 input lock
  -> wakeword.pause()
  -> 关闭 wakeword 的录音 stream
  -> mode = push_to_talk
  -> 释放 input lock
```

只有完成 pause 后，`push_to_talk` 才被标记为已生效。

### push_to_talk -> wakeword

```text
等待 listen_once() 释放 input lock
  -> wakeword.resume()
  -> mode = wakeword
```

## 6. wakeword 模型不会因为切模式被卸载

切到 `push_to_talk` 使用：

```python
wakeword.pause()
```

而不是：

```python
wakeword.close()
```

因此：

- openWakeWord：关闭 `sounddevice.InputStream`，保留已经加载的模型；
- Porcupine：关闭 recorder，保留 Porcupine engine；
- 再切回 `wakeword` 时调用 `resume()`；
- 只有 `service.close()` 才最终释放 wakeword 模型/engine。

ASR、SpeechController、Windows SAPI、Gateway client 和 STT HTTP server 都不会因为模式切换被重新创建。

## 7. 模式真正生效后的事件

当模式切换最终生效时，Voice Core 通过已有 `idle` 事件发确认，不新增 UI 专用 event kind：

```python
VoiceEvent(
    kind=VoiceEventKind.IDLE,
    metadata={
        "source": "input_mode",
        "input_mode": "push_to_talk",
        "mode_change": "applied",
    },
)
```

因此 bridge 可以：

1. 根据 `set_input_mode()` 返回值立即回复 `applied` 或 `pending`；
2. 若 pending，等收到 `source=input_mode` 的事件后再向桌宠确认最终生效。

## 8. 桌宠 bridge 推荐命令

桌宠不需要直接理解 wakeword backend，只需要向 bridge 发一个模式命令，例如：

```json
{
  "type": "set_voice_input_mode",
  "mode": "push_to_talk"
}
```

bridge：

```python
def handle_set_voice_input_mode(command):
    mode = command["mode"]
    applied = service.set_input_mode(mode)

    return {
        "type": "voice_input_mode_result",
        "status": "applied" if applied else "pending",
        "mode": service.get_input_mode(),
        "pending_mode": service.get_pending_input_mode(),
    }
```

桌宠按钮：

```python
# 当前为 push_to_talk 时
bridge.send({"type": "listen_once"})
```

bridge worker：

```python
service.listen_once(
    speak=True,
    metadata={"source": "desktop_pet"},
)
```

`listen_once()` 是阻塞的一轮调用，仍应放在 bridge worker / 线程池中，而不是 GUI 主线程。

## 9. 推荐状态关系

```text
                       set_input_mode("push_to_talk")
          +------------------------------------------------+
          |                                                v
+------------------+                               +------------------+
|     wakeword     |                               |   push_to_talk   |
| wakeword resume  |                               | wakeword paused  |
+--------+---------+                               +---------+--------+
         |                                                   |
         | wake detected                                     | listen_once()
         v                                                   v
+--------------------------------------------------------------------+
|                 shared recorded conversation turn                  |
| recording -> ASR -> Gateway -> TTS -> idle                         |
+--------------------------------------------------------------------+
         |                                                   |
         +---------------- current mode / pending -------------------+
```

模式只是决定“下一次麦克风输入由谁触发”，不会替换后面的 ASR / Gateway / TTS 链路。

## 10. 关闭

最终退出桌宠/bridge 时仍然只调用：

```python
service.close()
```

`close()` 才会释放：

- wakeword；
- SpeechController / TTS；
- STT server；
- Gateway；
- runtime resources。

模式切换本身不执行这些关闭操作。
