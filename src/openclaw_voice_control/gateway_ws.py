from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import OpenClawConfig


_SENTENCE_PATTERN = re.compile(r"[^。！？\n.!?]+[。！？\n.!?]")
_NOISE_KEYWORDS = (
    "Command still",
    "FileNotFound",
    "(no output)",
    "Use process",
    "No module",
    "未找到文件",
)


def _extract_complete_sentences(buffer: str) -> tuple[list[str], str]:
    matches = list(_SENTENCE_PATTERN.finditer(buffer))
    if not matches:
        return [], buffer
    last_end = matches[-1].end()
    sentences = [match.group(0).strip() for match in matches if match.group(0).strip()]
    return sentences, buffer[last_end:]


class ResponseAccumulator:
    """Merge ordered response snapshots without re-emitting already delivered text."""

    def __init__(self) -> None:
        self.full_text = ""
        self._emitted_chars = 0

    def feed_snapshot(self, snapshot: str) -> list[str]:
        snapshot = snapshot or ""
        if not snapshot:
            return []

        previous = self.full_text
        if snapshot == previous or previous.startswith(snapshot):
            return []

        if previous and not snapshot.startswith(previous):
            emitted_prefix = previous[: self._emitted_chars]
            if not snapshot.startswith(emitted_prefix):
                # Do not let a conflicting source rewrite text that has already been spoken.
                return []

        self.full_text = snapshot
        pending = self.full_text[self._emitted_chars :]
        sentences, remainder = _extract_complete_sentences(pending)
        if sentences:
            consumed = len(pending) - len(remainder)
            self._emitted_chars += consumed
        return sentences

    def flush(self) -> str:
        remainder = self.full_text[self._emitted_chars :].strip()
        self._emitted_chars = len(self.full_text)
        return remainder


@dataclass(slots=True)
class GatewayWebSocket:
    config: OpenClawConfig
    _ws: Any | None = field(default=None, init=False)
    _connected: bool = field(default=False, init=False)
    _req_id: int = field(default=0, init=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False)
    _pending_events: list[dict[str, Any]] = field(default_factory=list, init=False)

    def connect(self) -> None:
        if self._connected and self._ws is not None:
            return
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(self._connect_async())
        except Exception:
            self.close()
            raise

    async def _connect_async(self) -> None:
        import websockets

        self._ws = await websockets.connect(
            self.config.ws_url,
            max_size=2**24,
            proxy=None,
            open_timeout=float(self.config.ws_timeout),
            user_agent_header=None,
            additional_headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        self._connected = True
        self._req_id = 0
        self._pending_events.clear()

        raw = await asyncio.wait_for(self._ws.recv(), timeout=float(self.config.ws_timeout))
        challenge = json.loads(raw)
        if challenge.get("event") != "connect.challenge":
            raise RuntimeError(f"Expected connect.challenge, got: {challenge.get('event')}")

        await self._send_async(
            "connect",
            {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": "gateway-client",
                    "version": "1.0.0",
                    "platform": "windows",
                    "mode": "backend",
                },
                "role": "operator",
                "scopes": ["operator.read", "operator.write"],
                "auth": {"token": self.config.token},
                "caps": [],
                "commands": [],
            },
        )

        raw = await asyncio.wait_for(self._ws.recv(), timeout=float(self.config.ws_timeout))
        hello = json.loads(raw)
        if not hello.get("ok"):
            raise RuntimeError(
                f"WebSocket connect failed: {hello.get('error', {}).get('message', 'unknown')}"
            )
        granted = hello.get("payload", {}).get("auth", {}).get("scopes", [])
        if "operator.write" not in granted:
            raise RuntimeError(f"Granted scopes {granted} missing operator.write")

    async def _send_async(self, method: str, params: dict[str, Any]) -> int:
        if self._ws is None:
            raise RuntimeError("WebSocket is not connected")
        self._req_id += 1
        await self._ws.send(
            json.dumps(
                {
                    "type": "req",
                    "id": str(self._req_id),
                    "method": method,
                    "params": params,
                }
            )
        )
        return self._req_id

    async def _recv_response_async(self, expected_id: int, timeout: float) -> dict[str, Any] | None:
        if self._ws is None:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=min(0.5, remaining))
            except asyncio.TimeoutError:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "res" and msg.get("id") == str(expected_id):
                return msg
            if msg.get("type") == "event":
                self._pending_events.append(msg)
        return None

    def chat_send_streaming(
        self,
        text: str,
        on_sentence: Callable[[str], None] | None = None,
    ) -> str:
        if not self._connected or self._ws is None:
            self.connect()
        assert self._loop is not None
        return self._loop.run_until_complete(self._chat_send_streaming_async(text, on_sentence))

    async def _chat_send_streaming_async(
        self,
        text: str,
        on_sentence: Callable[[str], None] | None = None,
    ) -> str:
        if self._ws is None:
            raise RuntimeError("WebSocket is not connected")

        tagged_text = f"\U0001f3a4 {text}"
        # Record before chat.send so a slow ACK cannot move the session matching window forward.
        send_timestamp = time.time()
        req_id = await self._send_async(
            "chat.send",
            {
                "sessionKey": self.config.session_key,
                "message": tagged_text,
                "idempotencyKey": str(uuid.uuid4()),
            },
        )

        response = await self._recv_response_async(req_id, timeout=float(self.config.ws_timeout))
        if response is None:
            self._get_logger().warning("chat.send ack timeout")
            return ""
        if not response.get("ok"):
            return ""

        run_id = response.get("payload", {}).get("runId", "")
        log = self._get_logger()
        log.info("Chat sent, runId=%s", run_id)
        accumulator = ResponseAccumulator()
        stable_polls = 0
        deadline = time.monotonic() + float(self.config.timeout_seconds)
        session_dir = self._session_dir()

        while time.monotonic() < deadline:
            changed = False

            events = self._pending_events
            self._pending_events = []
            event = await self._recv_event_nonblocking(timeout=0.2)
            if event is not None:
                events.append(event)

            for evt in events:
                snapshot = self._agent_snapshot(evt, run_id)
                if snapshot is None:
                    continue
                before = accumulator.full_text
                sentences = accumulator.feed_snapshot(snapshot)
                changed = changed or accumulator.full_text != before
                self._deliver_sentences(sentences, on_sentence)

            session_snapshot = self._read_session_snapshot(
                session_dir,
                send_timestamp=send_timestamp,
                tagged="\U0001f3a4",
            )
            if session_snapshot:
                before = accumulator.full_text
                sentences = accumulator.feed_snapshot(session_snapshot)
                changed = changed or accumulator.full_text != before
                self._deliver_sentences(sentences, on_sentence)

            if accumulator.full_text:
                stable_polls = 0 if changed else stable_polls + 1
                if stable_polls >= 3:
                    remainder = accumulator.flush()
                    if remainder and on_sentence is not None:
                        on_sentence(remainder)
                    return accumulator.full_text

            await asyncio.sleep(0.2)

        log.warning("Response deadline reached")
        remainder = accumulator.flush()
        if remainder and on_sentence is not None:
            on_sentence(remainder)
        return accumulator.full_text

    async def _recv_event_nonblocking(self, timeout: float) -> dict[str, Any] | None:
        if self._ws is None:
            return None
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if msg.get("type") == "event":
            return msg
        return None

    @staticmethod
    def _agent_snapshot(event: dict[str, Any], run_id: str) -> str | None:
        if event.get("type") != "event" or event.get("event") != "agent":
            return None
        payload = event.get("payload", {})
        if payload.get("runId") != run_id:
            return None
        data = payload.get("data", {})
        output = data.get("output")
        return output if isinstance(output, str) and output else None

    @staticmethod
    def _deliver_sentences(
        sentences: list[str],
        on_sentence: Callable[[str], None] | None,
    ) -> None:
        if on_sentence is None:
            return
        for sentence in sentences:
            on_sentence(sentence)

    def _session_dir(self) -> Path:
        configured_home = getattr(self.config, "home_dir", None)
        env_home = os.getenv("OPENCLAW_HOME")
        home = Path(configured_home or env_home or (Path.home() / ".openclaw")).expanduser()
        return home / "agents" / self.config.agent_id / "sessions"

    def _read_session_snapshot(
        self,
        session_dir: Path,
        *,
        send_timestamp: float,
        tagged: str,
    ) -> str | None:
        if not session_dir.exists():
            return None
        try:
            files = sorted(
                [
                    path
                    for path in session_dir.glob("*.jsonl")
                    if not path.name.endswith(".trajectory.jsonl")
                ],
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None
        if not files:
            return None

        try:
            lines = files[0].read_text(encoding="utf-8").splitlines()
        except OSError:
            return None

        found_idx = -1
        for index in range(len(lines) - 1, -1, -1):
            try:
                entry = json.loads(lines[index])
            except (json.JSONDecodeError, TypeError):
                continue
            message = entry.get("message", {})
            if message.get("role") != "user":
                continue
            if not self._timestamp_is_current(entry.get("timestamp"), send_timestamp):
                continue
            if tagged in self._message_text(message.get("content", "")):
                found_idx = index
                break

        if found_idx < 0:
            return None

        latest: str | None = None
        for line in lines[found_idx + 1 :]:
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            message = entry.get("message", {})
            if message.get("role") != "assistant":
                continue
            text = self._message_text(message.get("content", ""), minimum_length=4)
            if text and not any(keyword in text for keyword in _NOISE_KEYWORDS):
                latest = text
        return latest

    @staticmethod
    def _timestamp_is_current(value: Any, send_timestamp: float) -> bool:
        if not isinstance(value, str) or not value:
            return True
        try:
            if value.endswith("Z"):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp() >= send_timestamp - 1.0
        except ValueError:
            return True

    @staticmethod
    def _message_text(content: Any, minimum_length: int = 0) -> str:
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text":
                continue
            text = str(part.get("text", "")).strip()
            if text and len(text) >= minimum_length:
                parts.append(text)
        return "".join(parts)

    @staticmethod
    def _get_logger():
        import logging

        return logging.getLogger("openclaw.voice_control")

    def close(self) -> None:
        ws = self._ws
        loop = self._loop
        self._ws = None
        self._connected = False
        self._pending_events.clear()

        if ws is not None and loop is not None and not loop.is_closed():
            try:
                loop.run_until_complete(ws.close())
            except Exception:
                pass
        if loop is not None and not loop.is_closed():
            loop.close()
        self._loop = None
