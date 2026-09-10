# Gateway WebSocket

`gateway_ws.py` implements the OpenClaw Gateway transport used by `openclaw_client.py`.

## Protocol requirement

The current Voice Core requires **OpenClaw Gateway protocol v4**. During the `connect` handshake it sends:

```json
{
  "minProtocol": 4,
  "maxProtocol": 4
}
```

A Gateway that only supports an older protocol version is not compatible with this client. Keep the OpenClaw Gateway installation updated to a version that supports protocol v4.

## Gateway v4 agent reply stream

After `chat.send` is accepted, its response supplies a `runId`. That ACK only means the run was accepted; it is not the assistant reply.

Voice Core then follows `event.agent` frames for that exact run. The v4 shape used by the normal reply path is:

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "...",
    "seq": 1,
    "stream": "assistant",
    "ts": 0,
    "data": {
      "text": "cumulative assistant text",
      "delta": "new text"
    }
  }
}
```

For `stream="assistant"`, `data.text` is treated as the cumulative visible assistant snapshot and is fed into the existing `ResponseAccumulator`. Complete sentences are delivered in order through `on_sentence` without duplicate playback.

Events whose `payload.runId` does not match the `chat.send` run are ignored. The run finishes when a matching agent event has `stream="lifecycle"` and `data.phase="end"` or `"error"`; any remaining unspoken tail is flushed and the method returns immediately instead of waiting for the overall response deadline.

`data.output` remains accepted only as a legacy compatibility fallback. Protocol v4 normally uses `data.text`.

The same-machine session JSONL reader remains available as a fallback, but it is not required for the normal Gateway v4 path. This matters when Voice Core and OpenClaw run on different machines or VMs and do not share the OpenClaw sessions directory.

## Safe event diagnostics

Gateway agent events may be logged at debug level using structure-only diagnostics. The log records event type/name, runId, payload/data field names and text length. It does **not** log auth tokens or full assistant/event text.

## Connection and response flow

The client connects to the configured WS URL, completes the challenge/auth handshake, sends `chat.send`, and receives `event.agent` snapshots. It also polls the configured OpenClaw session directory as a fallback source for the final assistant message.

Both sources feed one response accumulator. A source may only advance the known text; already-seen prefixes are ignored. Complete sentences are delivered synchronously and in order to the caller callback, so the speech layer does not receive duplicate sentences and no per-sentence callback threads are created.

The send timestamp is recorded before the request is sent so session-file matching cannot miss a user message merely because the Gateway ACK was delayed.

Timeouts are configuration-driven:

- `OPENCLAW_WS_TIMEOUT`: websocket connection / ACK wait budget.
- `OPENCLAW_TIMEOUT_SECONDS`: total response wait budget.

Session lookup starts under `OPENCLAW_HOME/agents/<agent_id>/sessions` and excludes trajectory logs.

Websocket proxy disabling is passed to the websocket connection itself. The process-wide proxy environment is not mutated.

`GatewayWebSocket.close()` closes the websocket on its synchronous wrapper event loop and then closes the loop, making repeated client teardown safe at service shutdown.
