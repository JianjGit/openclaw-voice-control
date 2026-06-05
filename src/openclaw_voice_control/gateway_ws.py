from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import OpenClawConfig


# Sentence boundary pattern: split on Chinese/English punctuation + newline
_SENTENCE_SPLIT = re.compile(r"([^。！？\n.!?]+[。！？\n.!?])")


def _extract_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """Split accumulated text into complete sentences + remaining fragment."""
    sentences = _SENTENCE_SPLIT.findall(buffer)
    if not sentences:
        return [], buffer
    complete = [s.strip() for s in sentences if s.strip()]
    consumed = len("".join(sentences))
    remainder = buffer[consumed:].strip()
    return complete, remainder


@dataclass(slots=True)
class GatewayWebSocket:
    config: OpenClawConfig
    _ws: Any | None = field(default=None, init=False)
    _connected: bool = field(default=False, init=False)
    _req_id: int = field(default=0, init=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False)

    def connect(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_async())

    async def _connect_async(self) -> None:
        import websockets

        # Nuke ALL proxy env vars before any connection attempt
        for key in list(os.environ.keys()):
            if "proxy" in key.lower():
                os.environ.pop(key, None)

        ws_url = self.config.ws_url

        self._ws = await websockets.connect(
            ws_url,
            max_size=2**24,
            proxy=None,
            open_timeout=15,
            user_agent_header=None,
            additional_headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },

        )
        self._connected = True
        self._req_id = 0

        # Receive connect.challenge
        raw = await self._ws.recv()
        challenge = json.loads(raw)
        if challenge.get("event") != "connect.challenge":
            raise RuntimeError(f"Expected connect.challenge, got: {challenge.get('event')}")

        # Send connect with token auth
        await self._send_async("connect", {
            "minProtocol": 3, "maxProtocol": 3,
            "client": {"id": "gateway-client", "version": "1.0.0", "platform": "windows", "mode": "backend"},
            "role": "operator",
            "scopes": ["operator.read", "operator.write"],
            "auth": {"token": self.config.token},
            "caps": [], "commands": [],
        })

        # Receive hello-ok
        raw = await self._ws.recv()
        hello = json.loads(raw)
        if not hello.get("ok"):
            raise RuntimeError(f"WebSocket connect failed: {hello.get('error', {}).get('message', 'unknown')}")

        # Verify scopes
        granted = hello.get("payload", {}).get("auth", {}).get("scopes", [])
        if "operator.write" not in granted:
            raise RuntimeError(f"Granted scopes {granted} missing operator.write")

    async def _send_async(self, method: str, params: dict) -> int:
        self._req_id += 1
        await self._ws.send(json.dumps({
            "type": "req", "id": str(self._req_id), "method": method, "params": params,
        }))
        return self._req_id

    async def _recv_response_async(self, expected_id: int, timeout: float = 10.0) -> dict | None:
        """Wait for a response with the matching id, with a timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=1.0)
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "res" and msg.get("id") == str(expected_id):
                    return msg
            except asyncio.TimeoutError:
                continue
        return None

    def chat_send_streaming(
        self,
        text: str,
        on_sentence: Callable[[str], None] | None = None,
    ) -> str:
        if not self._connected or self._ws is None:
            self.connect()
        return self._loop.run_until_complete(
            self._chat_send_streaming_async(text, on_sentence)
        )

    async def _chat_send_streaming_async(
        self,
        text: str,
        on_sentence: Callable[[str], None] | None = None,
    ) -> str:
        tagged_text = f"\U0001f3a4 {text}"
        req_id = await self._send_async("chat.send", {
            "sessionKey": self.config.session_key,
            "message": tagged_text,
            "idempotencyKey": str(uuid.uuid4()),
        })

        # Wait for ack with timeout (10s)
        response = await self._recv_response_async(req_id, timeout=10.0)
        if response is None:
            self._get_logger().warning("chat.send ack timeout")
            return ""
        if not response.get("ok"):
            return ""

        run_id = response.get("payload", {}).get("runId", "")
        _log = self._get_logger()
        _log.info("Chat sent, runId=%s", run_id)

        # Record the timestamp when we sent the message, to match against session file
        send_timestamp = time.time()
        _log.debug("send_timestamp=%.1f (for matching against session file)", send_timestamp)

        full_text = ""
        pending_buffer = ""
        stable_count = 0  # count how many polls with no change
        tagged = "\U0001f3a4"
        agent_id = self.config.agent_id
        session_dirs = [
            Path(r"E:\AppData\.openclaw\agents") / agent_id / "sessions",
            Path(os.path.expanduser("~")) / ".openclaw" / "agents" / agent_id / "sessions",
        ]
        deadline = time.time() + 120  # 2 min timeout

        while time.time() < deadline:
            # Drain any WebSocket events (non-blocking) for potential future streaming
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=0.2)
                evt = json.loads(raw)
                if evt.get("type") == "event" and evt.get("event") == "agent":
                    p = evt.get("payload", {})
                    if p.get("runId") == run_id:
                        d = p.get("data", {})
                        if d.get("output"):
                            new_output = d["output"]
                            if len(new_output) > len(full_text):
                                new_chunk = new_output[len(full_text):]
                                full_text = new_output
                                pending_buffer += new_chunk
                                stable_count = 0
                                # Deliver complete sentences in background thread
                                if on_sentence:
                                    sentences, pending_buffer = _extract_complete_sentences(pending_buffer)
                                    for sentence in sentences:
                                        if sentence:
                                            _log.debug("Stream sentence: [%s]", sentence[:60])
                                            threading.Thread(
                                                target=on_sentence,
                                                args=(sentence,),
                                                daemon=True,
                                            ).start()
                        if d.get("output") and d.get("finish_reason"):
                            pass  # Stream is complete for this event type
            except asyncio.TimeoutError:
                pass

            # Primary: poll session file for the complete assistant message
            for sd in session_dirs:
                if not sd.exists():
                    continue
                try:
                    # Exclude .trajectory.jsonl files - they are trace logs, not session files
                    files = sorted(
                        [f for f in sd.glob("*.jsonl") if not f.name.endswith(".trajectory.jsonl")],
                        key=os.path.getmtime, reverse=True
                    )
                    if not files:
                        continue
                    with open(str(files[0]), "r", encoding="utf-8") as f:
                        lines = f.read().splitlines()
                    # Find the user message that matches our request (by timestamp)
                    # We look for a user message with:
                    # 1. 🎤 tag (sent by voice-control)
                    # 2. timestamp >= send_timestamp (our current request)
                    found_idx = -1
                    for i in range(len(lines) - 1, -1, -1):
                        if not lines[i].strip():
                            continue
                        try:
                            e = json.loads(lines[i])
                            if e.get("message", {}).get("role") == "user":
                                # Check timestamp first
                                msg_ts = e.get("timestamp", "")
                                if msg_ts:
                                    # Parse ISO timestamp and compare
                                    try:
                                        from datetime import datetime, timezone
                                        # Handle both with and without timezone
                                        if msg_ts.endswith("Z"):
                                            msg_dt = datetime.fromisoformat(msg_ts.replace("Z", "+00:00"))
                                        else:
                                            # If no timezone, assume UTC (session file uses UTC)
                                            msg_dt = datetime.fromisoformat(msg_ts).replace(tzinfo=timezone.utc)
                                        msg_unix = msg_dt.timestamp()
                                        if msg_unix < send_timestamp - 1:  # 1s tolerance
                                            continue  # This is an old message, skip
                                    except Exception:
                                        pass
                                # Check for 🎤 tag
                                c = e["message"].get("content", "")
                                txt = ""
                                if isinstance(c, str):
                                    txt = c
                                elif isinstance(c, list):
                                    for p in c:
                                        if p.get("type") == "text":
                                            txt = p.get("text", "")
                                            break
                                if tagged in txt:
                                    _log.debug("Found matching user message at line %d, ts=%s, msg_unix=%.1f, send_ts=%.1f",
                                               i, msg_ts, msg_unix, send_timestamp)
                                    found_idx = i
                                    break
                        except Exception:
                            continue
                    if found_idx >= 0:
                        for j in range(found_idx + 1, len(lines)):
                            try:
                                a = json.loads(lines[j])
                                if a.get("message", {}).get("role") != "assistant":
                                    continue
                                c = a["message"].get("content", "")
                                if not isinstance(c, list):
                                    continue
                                texts = []
                                for part in c:
                                    if part.get("type") == "text":
                                        t = part.get("text", "").strip()
                                        if t and len(t) > 3:
                                            texts.append(t)
                                if texts:
                                    combined = "".join(texts)
                                    noise_keywords = ["Command still", "FileNotFound", "(no output)", "Use process", "No module", "未找到文件"]
                                    is_noise = any(k in combined for k in noise_keywords)
                                    if not is_noise and len(combined) > len(full_text):
                                        # New/changed text from session file
                                        new_chunk = combined[len(full_text):]
                                        full_text = combined
                                        pending_buffer += new_chunk
                                        stable_count = 0
                                        _log.debug("Session file text (+%d): [%s]", len(new_chunk), new_chunk[:60])
                                        # Deliver complete sentences
                                        if on_sentence:
                                            sentences, pending_buffer = _extract_complete_sentences(pending_buffer)
                                            for sentence in sentences:
                                                if sentence:
                                                    _log.debug("Stream sentence: [%s]", sentence[:60])
                                                    threading.Thread(
                                                        target=on_sentence,
                                                        args=(sentence,),
                                                        daemon=True,
                                                    ).start()
                            except Exception:
                                continue
                except Exception:
                    continue

            # If text is stable for 3 polls (1.5s) and has content, we're done
            if full_text:
                stable_count += 1
                if stable_count >= 3:
                    _log.info("Response stable for 3 polls, final text: [%s]", full_text[:80])
                    # Speak any remaining fragment
                    if on_sentence and pending_buffer.strip():
                        remainder = pending_buffer.strip()
                        _log.debug("Final fragment: [%s]", remainder[:60])
                        threading.Thread(
                            target=on_sentence,
                            args=(remainder,),
                            daemon=True,
                        ).start()
                    return full_text

            await asyncio.sleep(0.2)

        # Timeout: return whatever we have
        _log.warning("Response deadline reached")
        if on_sentence and pending_buffer.strip():
            threading.Thread(
                target=on_sentence,
                args=(pending_buffer.strip(),),
                daemon=True,
            ).start()
        return full_text

    def _get_logger(self):
        import logging
        return logging.getLogger("openclaw.voice_control")

    def close(self) -> None:
        if self._ws is not None:
            try:
                if self._loop and self._loop.is_running():
                    self._loop.run_until_complete(self._ws.close())
            except Exception:
                pass
            self._ws = None
            self._connected = False
        if self._loop:
            self._loop.close()
            self._loop = None
