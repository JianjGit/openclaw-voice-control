from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .config import OpenClawConfig
from .gateway_ws import GatewayWebSocket


@dataclass(slots=True)
class OpenClawClient:
    config: OpenClawConfig
    _ws: GatewayWebSocket | None = None

    def ask(self, user_text: str) -> str:
        """Blocking call: send message, wait for full reply, return text."""
        if self._ws is None:
            self._ws = GatewayWebSocket(self.config)
        return self._ws.chat_send_streaming(user_text, on_sentence=None)

    def ask_streaming(self, user_text: str, on_sentence: Callable[[str], None]) -> str:
        """Streaming call: send message, deliver each complete sentence via callback,
        return full text when done."""
        if self._ws is None:
            self._ws = GatewayWebSocket(self.config)
        return self._ws.chat_send_streaming(user_text, on_sentence=on_sentence)

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
