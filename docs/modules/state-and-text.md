# 状态管理 & 文本清理流程

> 源码：`src/openclaw_voice_control/state.py`, `text.py`

---

## 状态管理 (OverlayStateManager)

```
1. [@OpenclawVoiceControl] write(): 将状态 JSON 原子写入 runtime/overlay_state.json
2. request_stop(): 创建 runtime/stop_tts.flag 文件（存在=停止请求）
3. clear_stop_flag(): 删除 flag 文件
4. is_stop_requested(): 检查 flag 文件是否存在
```

## Overlay 轮询

```
5. [@OpenclawVoiceControl] QTimer 每 150ms 检查 state_file 的 mtime
  5.1 文件变化 → 读取 JSON → 更新悬浮窗 UI (status, user_text, reply_text)
```

## 文本清理 (text.py)

```
6. [@OpenclawVoiceControl] clean_text_for_overlay({text}) → str
  6.1 依次移除: 代码块、行内代码、链接、图片、标题、引用、列表、粗斜体、Emoji
  6.2 合并多余空白

7. clean_text_for_tts({text}) → str
  7.1 先执行 clean_text_for_overlay
  7.2 换行 → 句号，合并重复标点，末尾补句号
```
