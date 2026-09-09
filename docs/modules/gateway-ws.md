# Gateway WebSocket

`gateway_ws.py` implements the OpenClaw Gateway transport used by `openclaw_client.py`.

The client connects to the configured WS URL, completes the challenge/auth handshake, sends `chat.send`, and receives `event.agent` snapshots. It also polls the configured OpenClaw session directory as a fallback source for the final assistant message.

Both sources feed one response accumulator. A source may only advance the known text; already-seen prefixes are ignored. Complete sentences are delivered synchronously and in order to the caller callback, so the speech layer does not receive duplicate sentences and no per-sentence callback threads are created.

The send timestamp is recorded before the request is sent so session-file matching cannot miss a user message merely because the Gateway ACK was delayed.

Timeouts are configuration-driven:

- `OPENCLAW_WS_TIMEOUT`: websocket connection / ACK wait budget.
- `OPENCLAW_TIMEOUT_SECONDS`: total response wait budget.

Session lookup starts under `OPENCLAW_HOME/agents/<agent_id>/sessions` and excludes trajectory logs.

Websocket proxy disabling is passed to the websocket connection itself. The process-wide proxy environment is not mutated.

`GatewayWebSocket.close()` closes the websocket on its synchronous wrapper event loop and then closes the loop, making repeated client teardown safe at service shutdown.
