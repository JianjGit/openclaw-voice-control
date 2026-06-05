# Gateway WebSocket 通信流程

> 源码：`src/openclaw_voice_control/gateway_ws.py` — `GatewayWebSocket`（WebSocket 客户端，运行在 VoiceControl 进程中）
> 封装层：`openclaw_client.py` — `OpenClawClient`

---

## 连接

```
1. [@OpenclawVoiceControl] connect()
  1.1 清除所有代理环境变量
  1.2 通过 WebSocket 连接 ws://127.0.0.1:18789/ws
  1.3 完成 connect.challenge → connect(token 认证) → hello-ok 握手
```

## 流式发送与接收

```
2. [@OpenclawVoiceControl] chat_send_streaming({text}, {on_sentence}) → str
  2.1 通过 WebSocket 向 [@OpenclawGateway] 发送 RPC 方法 chat.send
    2.1.1 消息内容添加 🎤 前缀标记
    2.1.2 [@OpenclawGateway] 在 dashboard 显示消息
    2.1.3 [@OpenclawGateway] 转发给 [@AiApiBackend] 生成回复
  2.2 [@OpenclawGateway] 返回 ack → 获取 {run_id}
  2.3 记录 {send_timestamp} = time.time() # 用于匹配 session 文件中的消息

  2.4 WHILE 总计时 120s 内 (每轮 ~0.4s):
    # ★ 每轮先 A 后 B，串行执行
    2.4.1 路径A: 接收 WebSocket event.agent 事件 (0.2s 超时)
      2.4.1.1 IF 有事件且 runId 匹配 → 提取 [@AiApiBackend] 的增量回复文本
    2.4.2 路径B: 扫描 session .jsonl 文件 → 查找当前消息的 assistant 回复
      2.4.2.1 排除 .trajectory.jsonl 文件 (trace 日志，非主会话文件)
      2.4.2.2 从后往前遍历，查找带 🎤 标记的 user 消息
      2.4.2.3 ★ 时间戳匹配: msg_unix >= send_timestamp - 1
        - 确保找到的是当前发送的消息，而非上一条旧消息
        - 时间戳解析: 带 Z 后缀视为 UTC，不带时区也假设为 UTC
      2.4.2.4 找到 user 消息后，查找其后的 assistant 回复
    2.4.3 IF 有新文本: 正则分句 → threading.Thread 调用 {on_sentence} 回调
    2.4.4 IF 连续 3 轮无新文本: BREAK
    2.4.5 sleep 0.2s → 下一轮
```

## 关键实现细节

### Session 文件匹配逻辑

为避免"播报上一条消息的回复"问题，采用时间戳匹配：

1. **发送时记录时间戳**: `send_timestamp = time.time()`
2. **轮询时匹配**: 只查找时间戳 >= `send_timestamp - 1` 的 user 消息
3. **从后往前遍历**: 确保找到最新的匹配消息

### 文件过滤

- Session 目录中存在两类 `.jsonl` 文件：
  - `xxx.jsonl`: 主会话文件，包含完整的对话历史
  - `xxx.trajectory.jsonl`: Trace 日志，用于调试
- 轮询时必须排除 `.trajectory.jsonl`，否则无法找到 assistant 消息

### 时间戳解析

Session 文件中的时间戳格式为 ISO 8601 (UTC)：
- 带 Z 后缀: `2026-05-16T13:22:56.333Z`
- 不带时区: `2026-05-16T13:22:56.333`

解析时需正确处理时区：
```python
if msg_ts.endswith("Z"):
    msg_dt = datetime.fromisoformat(msg_ts.replace("Z", "+00:00"))
else:
    # 假设为 UTC，避免被错误当作本地时间
    msg_dt = datetime.fromisoformat(msg_ts).replace(tzinfo=timezone.utc)
```
