from __future__ import annotations

import asyncio
import inspect
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from openclaw_voice_control.gateway_ws import GatewayWebSocket, ResponseAccumulator


def _config(**overrides):
    values = {
        "ws_url": "ws://127.0.0.1:18789/ws",
        "token": "token",
        "session_key": "agent:main:main",
        "agent_id": "main",
        "ws_timeout": 7,
        "timeout_seconds": 19,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_be_t08_response_accumulator_dedupes_ws_and_session_snapshots() -> None:
    accumulator = ResponseAccumulator()

    assert accumulator.feed_snapshot("第一句。") == ["第一句。"]
    assert accumulator.feed_snapshot("第一句。") == []
    assert accumulator.feed_snapshot("第一句。第二句。") == ["第二句。"]
    assert accumulator.feed_snapshot("第一句。第二句。") == []
    assert accumulator.full_text == "第一句。第二句。"


def test_be_t08_accumulator_flushes_only_unspoken_tail() -> None:
    accumulator = ResponseAccumulator()

    assert accumulator.feed_snapshot("一句。未完成") == ["一句。"]
    assert accumulator.feed_snapshot("一句。未完成尾巴") == []
    assert accumulator.flush() == "未完成尾巴"
    assert accumulator.flush() == ""


def test_be_t08_session_fallback_reads_current_assistant_message(tmp_path) -> None:
    session_dir = tmp_path / "agents" / "main" / "sessions"
    session_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    session = session_dir / "session.jsonl"
    session.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": now.isoformat(), "message": {"role": "user", "content": "🎤 hello"}}, ensure_ascii=False),
                json.dumps({"timestamp": now.isoformat(), "message": {"role": "assistant", "content": [{"type": "text", "text": "fallback reply。"}]}}, ensure_ascii=False),
            ]
        ),
        encoding="utf-8",
    )
    gateway = GatewayWebSocket(_config())

    snapshot = gateway._read_session_snapshot(
        session_dir,
        send_timestamp=now.timestamp(),
        tagged="🎤",
    )

    assert snapshot == "fallback reply。"


def test_be_t08_handshake_uses_gateway_protocol_v4(monkeypatch) -> None:
    class FakeWS:
        def __init__(self) -> None:
            self.messages = [
                json.dumps({"event": "connect.challenge"}),
                json.dumps(
                    {
                        "ok": True,
                        "payload": {"auth": {"scopes": ["operator.read", "operator.write"]}},
                    }
                ),
            ]
            self.sent: list[dict] = []

        async def recv(self):
            return self.messages.pop(0)

        async def send(self, payload: str) -> None:
            self.sent.append(json.loads(payload))

    ws = FakeWS()

    class FakeWebsockets:
        @staticmethod
        async def connect(*_args, **_kwargs):
            return ws

    monkeypatch.setitem(sys.modules, "websockets", FakeWebsockets())
    gateway = GatewayWebSocket(_config())

    asyncio.run(gateway._connect_async())

    assert len(ws.sent) == 1
    connect_request = ws.sent[0]
    assert connect_request["method"] == "connect"
    assert connect_request["params"]["minProtocol"] == 4
    assert connect_request["params"]["maxProtocol"] == 4


def test_be_t08_v4_agent_events_stream_assistant_text_and_finish_on_lifecycle(tmp_path) -> None:
    run_id = "run-voice-1"

    def agent_event(event_run_id: str, seq: int, stream: str, data: dict) -> str:
        return json.dumps(
            {
                "type": "event",
                "event": "agent",
                "payload": {
                    "runId": event_run_id,
                    "seq": seq,
                    "stream": stream,
                    "ts": 1_700_000_000_000 + seq,
                    "data": data,
                },
            },
            ensure_ascii=False,
        )

    class FakeWS:
        def __init__(self) -> None:
            self.messages = [
                json.dumps(
                    {
                        "type": "res",
                        "id": "1",
                        "ok": True,
                        "payload": {"status": "accepted", "runId": run_id},
                    }
                ),
                agent_event("other-run", 1, "assistant", {"text": "这句绝不能串进来。"}),
                agent_event(run_id, 1, "assistant", {"text": "你好，旅行者。", "delta": "你好，旅行者。"}),
                agent_event(
                    run_id,
                    2,
                    "assistant",
                    {"text": "你好，旅行者。今天风很舒服。", "delta": "今天风很舒服。"},
                ),
                agent_event(run_id, 3, "lifecycle", {"phase": "end"}),
            ]
            self.sent: list[dict] = []

        async def recv(self):
            return self.messages.pop(0)

        async def send(self, payload: str) -> None:
            self.sent.append(json.loads(payload))

    ws = FakeWS()
    gateway = GatewayWebSocket(
        _config(home_dir=tmp_path, timeout_seconds=3, ws_timeout=1)
    )
    gateway._ws = ws
    callbacks: list[str] = []

    result = asyncio.run(gateway._chat_send_streaming_async("你好", callbacks.append))

    assert ws.sent[0]["method"] == "chat.send"
    assert result == "你好，旅行者。今天风很舒服。"
    assert callbacks == ["你好，旅行者。", "今天风很舒服。"]
    assert "这句绝不能串进来。" not in result


def test_be_t08_v4_agent_snapshot_uses_text_and_filters_unrelated_run() -> None:
    event = {
        "type": "event",
        "event": "agent",
        "payload": {
            "runId": "run-1",
            "seq": 2,
            "stream": "assistant",
            "ts": 123,
            "data": {"text": "累计回复。", "delta": "回复。"},
        },
    }

    assert GatewayWebSocket._agent_snapshot(event, "run-1") == "累计回复。"
    assert GatewayWebSocket._agent_snapshot(event, "run-2") is None


def test_be_t08_ack_wait_preserves_agent_event_order() -> None:
    class FakeWS:
        def __init__(self) -> None:
            self.messages = [
                json.dumps({"type": "event", "event": "agent", "payload": {"runId": "r1", "data": {"output": "早到事件。"}}}),
                json.dumps({"type": "res", "id": "1", "ok": True}),
            ]

        async def recv(self):
            return self.messages.pop(0)

    gateway = GatewayWebSocket(_config())
    gateway._ws = FakeWS()
    response = asyncio.run(gateway._recv_response_async(1, timeout=1.0))

    assert response is not None and response["ok"] is True
    assert len(gateway._pending_events) == 1
    assert gateway._pending_events[0]["event"] == "agent"


def test_be_t08_session_root_uses_openclaw_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCLAW_HOME", str(tmp_path))
    gateway = GatewayWebSocket(_config())
    assert gateway._session_dir() == tmp_path / "agents" / "main" / "sessions"


def test_be_t08_close_closes_ws_and_event_loop() -> None:
    class FakeWS:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    gateway = GatewayWebSocket(_config())
    loop = asyncio.new_event_loop()
    ws = FakeWS()
    gateway._loop = loop
    gateway._ws = ws
    gateway._connected = True

    gateway.close()

    assert ws.closed is True
    assert loop.is_closed()
    assert gateway._loop is None
    assert gateway._ws is None
    assert gateway._connected is False


def test_be_t08_gateway_source_has_configured_timeouts_ordered_callbacks_and_local_proxy_policy() -> None:
    source = inspect.getsource(GatewayWebSocket)
    method = inspect.getsource(GatewayWebSocket._chat_send_streaming_async)

    assert "self.config.ws_timeout" in source
    assert "self.config.timeout_seconds" in source
    assert method.index("send_timestamp = time.time()") < method.index("await self._send_async")
    assert "threading.Thread" not in source
    assert "os.environ.pop" not in source
    assert "proxy=None" in source
    assert "E:\\AppData" not in source
