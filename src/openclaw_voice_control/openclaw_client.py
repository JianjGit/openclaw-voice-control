from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .config import OpenClawConfig
from .gateway_ws import GatewayWebSocket


USER_TRANSCRIPT_MESSAGE_TEMPLATE = "语音：「{text}」"
DELIVERY_KIND_USER_TRANSCRIPT = "user_transcript"
DELIVERY_KIND_ASSISTANT_REPLY = "assistant_reply"


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
        user_delivery_key = str(uuid.uuid4())
        assistant_delivery_key = str(uuid.uuid4())
        self._mirror_user_transcript(user_text, idempotency_key=user_delivery_key)
        reply = self._gateway().chat_send_streaming(user_text, on_sentence=None)
        self._mirror_reply(reply, idempotency_key=assistant_delivery_key)
        return reply

    def ask_streaming(self, user_text: str, on_sentence: Callable[[str], None]) -> str:
        """Streaming call: send message, deliver each complete sentence via callback,
        return full text when done."""
        user_delivery_key = str(uuid.uuid4())
        assistant_delivery_key = str(uuid.uuid4())
        self._mirror_user_transcript(user_text, idempotency_key=user_delivery_key)
        reply = self._gateway().chat_send_streaming(user_text, on_sentence=on_sentence)
        self._mirror_reply(reply, idempotency_key=assistant_delivery_key)
        return reply

    def _mirror_user_transcript(self, user_text: str, *, idempotency_key: str | None = None) -> None:
        delivery = getattr(self.config, "delivery", None)
        if (
            delivery is None
            or delivery.mode != "mirror"
            or not delivery.include_user_transcript
            or not user_text
        ):
            return
        self._mirror_message(
            USER_TRANSCRIPT_MESSAGE_TEMPLATE.format(text=user_text),
            kind=DELIVERY_KIND_USER_TRANSCRIPT,
            idempotency_key=idempotency_key,
        )

    def _mirror_reply(self, reply: str, *, idempotency_key: str | None = None) -> None:
        delivery = getattr(self.config, "delivery", None)
        if delivery is None or delivery.mode != "mirror" or not reply:
            return
        self._mirror_message(
            reply,
            kind=DELIVERY_KIND_ASSISTANT_REPLY,
            idempotency_key=idempotency_key,
        )

    def _mirror_message(
        self,
        message: str,
        *,
        kind: str,
        idempotency_key: str | None = None,
    ) -> None:
        delivery = getattr(self.config, "delivery", None)
        if delivery is None or delivery.mode != "mirror":
            return

        targets = getattr(self.config, "delivery_targets", {})
        target = targets.get(delivery.target)
        if target is None:
            # load_config validates this. Keep manually-constructed configs fail-safe.
            self._log_delivery(kind, delivery.target, "", success=False)
            return

        # Generate one key per logical mirrored message and pass it unchanged to the
        # Gateway request. Voice Core does not auto-retry mirror delivery; if a send
        # attempt is retried in this call path, the same key must be reused.
        delivery_key = idempotency_key or str(uuid.uuid4())
        try:
            self._send_gateway_delivery(
                message,
                channel=target.channel,
                account_id=target.account_id,
                to=target.to,
                idempotency_key=delivery_key,
            )
        except Exception:
            # Do not leak provider/Gateway error details; delivery is best-effort and
            # must never change the original conversation reply or interrupt speech.
            self._log_delivery(kind, delivery.target, target.channel, success=False)
            return
        self._log_delivery(kind, delivery.target, target.channel, success=True)

    def _send_gateway_delivery(
        self,
        message: str,
        *,
        channel: str,
        account_id: str,
        to: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        gateway = self._gateway()
        if not gateway._connected or gateway._ws is None:
            gateway.connect()
        if gateway._loop is None or gateway._loop.is_closed():
            raise RuntimeError("Gateway event loop is unavailable")
        delivery_key = idempotency_key or str(uuid.uuid4())
        return gateway._loop.run_until_complete(
            self._send_gateway_delivery_async(
                gateway,
                message,
                channel=channel,
                account_id=account_id,
                to=to,
                idempotency_key=delivery_key,
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
        idempotency_key: str,
    ) -> dict[str, Any]:
        # Gateway protocol v4 exposes operator outbound delivery as `send`.
        # SendParamsSchema has no role/sender/metadata field, so user transcript
        # identity is represented only in the message text. Do not pass sessionKey:
        # OPENCLAW_SESSION_KEY selects the answering conversation only.
        request_id = await gateway._send_async(
            "send",
            {
                "to": to,
                "message": message,
                "channel": channel,
                "accountId": account_id,
                "agentId": self.config.agent_id,
                "idempotencyKey": idempotency_key,
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
    def _log_delivery(kind: str, target_name: str, channel: str, *, success: bool) -> None:
        logging.getLogger("openclaw.voice_control").log(
            logging.INFO if success else logging.WARNING,
            "Delivery mirror | kind=%s target=%s channel=%s success=%s",
            kind,
            target_name,
            channel,
            str(success).lower(),
        )

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
