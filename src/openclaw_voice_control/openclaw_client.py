from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .config import OpenClawConfig
from .gateway_ws import GatewayWebSocket


@dataclass(slots=True)
class OpenClawClient:
    config: OpenClawConfig
    _ws: GatewayWebSocket | None = None

    def _gateway(self) -> GatewayWebSocket:
        if self._ws is None:
            self._ws = GatewayWebSocket(self.config)
        return self._ws

    def ask(self, user_text: str) -> str:
        """Blocking call: send message, wait for full reply, return text."""
        reply = self._gateway().chat_send_streaming(user_text, on_sentence=None)
        self._mirror_reply(reply)
        return reply

    def ask_streaming(self, user_text: str, on_sentence: Callable[[str], None]) -> str:
        """Streaming call: send message, deliver each complete sentence via callback,
        return full text when done."""
        reply = self._gateway().chat_send_streaming(user_text, on_sentence=on_sentence)
        self._mirror_reply(reply)
        return reply

    def _mirror_reply(self, reply: str) -> None:
        delivery = getattr(self.config, "delivery", None)
        if delivery is None or delivery.mode != "mirror" or not reply:
            return

        targets = getattr(self.config, "delivery_targets", {})
        target = targets.get(delivery.target)
        if target is None:
            # load_config validates this. Keep manually-constructed configs fail-safe.
            self._log_delivery(delivery.target, "", success=False)
            return

        try:
            self._send_gateway_delivery(
                reply,
                channel=target.channel,
                account_id=target.account_id,
                to=target.to,
            )
        except Exception:
            # Do not leak provider/Gateway error details; delivery is best-effort and
            # must never change the original assistant reply or interrupt speech.
            self._log_delivery(delivery.target, target.channel, success=False)
            return
        self._log_delivery(delivery.target, target.channel, success=True)

    def _send_gateway_delivery(
        self,
        message: str,
        *,
        channel: str,
        account_id: str,
        to: str,
    ) -> dict[str, Any]:
        gateway = self._gateway()
        if not gateway._connected or gateway._ws is None:
            gateway.connect()
        if gateway._loop is None or gateway._loop.is_closed():
            raise RuntimeError("Gateway event loop is unavailable")
        return gateway._loop.run_until_complete(
            self._send_gateway_delivery_async(
                gateway,
                message,
                channel=channel,
                account_id=account_id,
                to=to,
            )
        )

    async def _send_gateway_delivery_async(
        self,
        gateway: GatewayWebSocket,
        message: str,
        *,
        channel: str,
        account_id: str,
        to: str,
    ) -> dict[str, Any]:
        # Gateway protocol v4 exposes operator outbound delivery as `send`.
        # Do not pass sessionKey: OPENCLAW_SESSION_KEY selects the answering
        # conversation only and is deliberately separate from mirror delivery.
        request_id = await gateway._send_async(
            "send",
            {
                "to": to,
                "message": message,
                "channel": channel,
                "accountId": account_id,
                "agentId": self.config.agent_id,
                "idempotencyKey": str(uuid.uuid4()),
            },
        )
        response = await gateway._recv_response_async(
            request_id,
            timeout=float(self.config.ws_timeout),
        )
        if response is None:
            raise RuntimeError("Gateway delivery response timeout")
        if not response.get("ok"):
            raise RuntimeError("Gateway delivery failed")
        payload = response.get("payload")
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _log_delivery(target_name: str, channel: str, *, success: bool) -> None:
        logging.getLogger("openclaw.voice_control").log(
            logging.INFO if success else logging.WARNING,
            "Delivery mirror | mode=mirror target=%s channel=%s success=%s",
            target_name,
            channel,
            str(success).lower(),
        )

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
