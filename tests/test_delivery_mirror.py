from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openclaw_voice_control.config import DeliveryConfig, DeliveryTargetConfig, load_config
from openclaw_voice_control.openclaw_client import OpenClawClient, USER_TRANSCRIPT_MESSAGE_TEMPLATE


class FakeGateway:
    def __init__(self) -> None:
        self.deliveries: list[dict[str, str]] = []
        self.sequence: list[str] = []

    def chat_send_streaming(self, _text: str, on_sentence=None) -> str:
        self.sequence.append("chat")
        if on_sentence is not None:
            self.sequence.append("sentence")
            on_sentence("最终回复。")
        return "最终回复。"

    def close(self) -> None:
        pass


def _client_config(
    mode: str = "off",
    target: str = "",
    include_user_transcript: bool = False,
):
    return SimpleNamespace(
        agent_id="main",
        ws_timeout=2,
        delivery=DeliveryConfig(
            mode=mode,
            target=target,
            include_user_transcript=include_user_transcript,
        ),
        delivery_targets={
            "feishu_jie": DeliveryTargetConfig(
                channel="feishu",
                account_id="default",
                to="user:ou_demo",
            )
        },
    )


def test_delivery_default_off_does_not_send() -> None:
    gateway = FakeGateway()

    class RecordingClient(OpenClawClient):
        def _send_gateway_delivery(self, message: str, **kwargs):
            gateway.deliveries.append({"message": message, **kwargs})
            return {}

    client = RecordingClient(_client_config(), _ws=gateway)  # type: ignore[arg-type]

    assert client.ask("hello") == "最终回复。"
    assert gateway.deliveries == []
    assert gateway.sequence == ["chat"]


def test_delivery_mirror_default_scope_sends_assistant_once() -> None:
    gateway = FakeGateway()

    class RecordingClient(OpenClawClient):
        def _send_gateway_delivery(self, message: str, **kwargs):
            gateway.deliveries.append({"message": message, **kwargs})
            return {}

    client = RecordingClient(_client_config("mirror", "feishu_jie"), _ws=gateway)  # type: ignore[arg-type]

    assert client.ask("hello") == "最终回复。"
    assert len(gateway.deliveries) == 1
    delivery = gateway.deliveries[0]
    assert delivery["message"] == "最终回复。"
    assert delivery["channel"] == "feishu"
    assert delivery["account_id"] == "default"
    assert delivery["to"] == "user:ou_demo"
    assert delivery["idempotency_key"]
    assert gateway.sequence == ["chat"]


def test_delivery_full_conversation_orders_user_chat_assistant() -> None:
    gateway = FakeGateway()

    class RecordingClient(OpenClawClient):
        def _send_gateway_delivery(self, message: str, **kwargs):
            gateway.sequence.append(f"delivery:{message}")
            gateway.deliveries.append({"message": message, **kwargs})
            return {}

    client = RecordingClient(
        _client_config("mirror", "feishu_jie", include_user_transcript=True),
        _ws=gateway,
    )  # type: ignore[arg-type]

    assert client.ask("你好") == "最终回复。"
    assert gateway.sequence == [
        "delivery:语音：「你好」",
        "chat",
        "delivery:最终回复。",
    ]
    assert [item["message"] for item in gateway.deliveries] == ["语音：「你好」", "最终回复。"]
    assert gateway.deliveries[0]["idempotency_key"] != gateway.deliveries[1]["idempotency_key"]


def test_user_transcript_delivery_failure_keeps_chat_reply_tts_and_assistant_attempt(caplog) -> None:
    gateway = FakeGateway()

    class UserFailingClient(OpenClawClient):
        def _send_gateway_delivery(self, message: str, **kwargs):
            gateway.sequence.append(f"delivery:{message}")
            if message.startswith("语音：「"):
                raise RuntimeError("provider secret detail that must not be logged")
            gateway.deliveries.append({"message": message, **kwargs})
            return {}

    client = UserFailingClient(
        _client_config("mirror", "feishu_jie", include_user_transcript=True),
        _ws=gateway,
    )  # type: ignore[arg-type]
    spoken: list[str] = []

    reply = client.ask_streaming("敏感语音内容", spoken.append)

    assert reply == "最终回复。"
    assert spoken == ["最终回复。"]
    assert gateway.sequence == [
        "delivery:语音：「敏感语音内容」",
        "chat",
        "sentence",
        "delivery:最终回复。",
    ]
    assert [item["message"] for item in gateway.deliveries] == ["最终回复。"]
    assert "kind=user_transcript" in caplog.text
    assert "target=feishu_jie" in caplog.text
    assert "channel=feishu" in caplog.text
    assert "success=false" in caplog.text
    assert "敏感语音内容" not in caplog.text
    assert "user:ou_demo" not in caplog.text
    assert "provider secret detail" not in caplog.text


def test_assistant_delivery_failure_keeps_reply_and_stream_callback() -> None:
    gateway = FakeGateway()

    class FailingClient(OpenClawClient):
        def _send_gateway_delivery(self, message: str, **_kwargs):
            gateway.sequence.append(f"delivery:{message}")
            if message == "最终回复。":
                raise RuntimeError("provider secret detail that must not be logged")
            return {}

    client = FailingClient(_client_config("mirror", "feishu_jie"), _ws=gateway)  # type: ignore[arg-type]
    spoken: list[str] = []

    reply = client.ask_streaming("hello", spoken.append)

    assert reply == "最终回复。"
    assert spoken == ["最终回复。"]
    assert gateway.sequence == ["chat", "sentence", "delivery:最终回复。"]


def test_streaming_sentences_do_not_duplicate_delivery() -> None:
    class MultiSentenceGateway(FakeGateway):
        def chat_send_streaming(self, _text: str, on_sentence=None) -> str:
            self.sequence.append("chat")
            if on_sentence is not None:
                on_sentence("第一句。")
                on_sentence("第二句。")
            return "第一句。第二句。"

    gateway = MultiSentenceGateway()

    class RecordingClient(OpenClawClient):
        def _send_gateway_delivery(self, message: str, **kwargs):
            gateway.deliveries.append({"message": message, **kwargs})
            return {}

    client = RecordingClient(
        _client_config("mirror", "feishu_jie", include_user_transcript=True),
        _ws=gateway,
    )  # type: ignore[arg-type]
    spoken: list[str] = []

    reply = client.ask_streaming("hello", spoken.append)

    assert reply == "第一句。第二句。"
    assert spoken == ["第一句。", "第二句。"]
    assert len(gateway.deliveries) == 2
    assert gateway.deliveries[0]["message"] == USER_TRANSCRIPT_MESSAGE_TEMPLATE.format(text="hello")
    assert gateway.deliveries[1]["message"] == reply


def test_gateway_v4_delivery_uses_send_rpc_without_session_or_fake_identity_fields() -> None:
    class RpcGateway:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def _send_async(self, method: str, params: dict) -> int:
            self.calls.append((method, params))
            return 7

        async def _recv_response_async(self, expected_id: int, timeout: float):
            assert expected_id == 7
            assert timeout == 2
            return {"type": "res", "id": "7", "ok": True, "payload": {"messageId": "m1"}}

    client = OpenClawClient(_client_config("mirror", "feishu_jie"))  # type: ignore[arg-type]
    gateway = RpcGateway()

    payload = asyncio.run(
        client._send_gateway_delivery_async(
            gateway,  # type: ignore[arg-type]
            "语音：「你好」",
            channel="feishu",
            account_id="default",
            to="user:ou_demo",
            idempotency_key="idem-user",
        )
    )

    assert payload == {"messageId": "m1"}
    assert len(gateway.calls) == 1
    method, params = gateway.calls[0]
    assert method == "send"
    assert params == {
        "to": "user:ou_demo",
        "message": "语音：「你好」",
        "channel": "feishu",
        "accountId": "default",
        "agentId": "main",
        "idempotencyKey": "idem-user",
    }
    assert "sessionKey" not in params
    assert "role" not in params
    assert "sender" not in params
    assert "metadata" not in params


def _write_config(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _clear_delivery_env(monkeypatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("OPENCLAW_DELIVERY_"):
            monkeypatch.delenv(key, raising=False)


def test_config_defaults_delivery_off_and_user_transcript_disabled(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    config = load_config(_write_config(tmp_path, "{}\n"))
    assert config.delivery.mode == "off"
    assert config.delivery.target == ""
    assert config.delivery.include_user_transcript is False
    assert config.delivery_targets == {}


def test_config_accepts_user_transcript_true(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(tmp_path, "delivery:\n  mode: off\n  include_user_transcript: true\n")
    config = load_config(path)
    assert config.delivery.include_user_transcript is True


def test_config_env_overrides_user_transcript_boolean(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(tmp_path, "delivery:\n  mode: off\n  include_user_transcript: false\n")
    monkeypatch.setenv("OPENCLAW_DELIVERY_INCLUDE_USER_TRANSCRIPT", "true")
    config = load_config(path)
    assert config.delivery.include_user_transcript is True


def test_config_rejects_invalid_user_transcript_boolean(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(tmp_path, "delivery:\n  mode: off\n  include_user_transcript: 'yes'\n")
    with pytest.raises(ValueError, match="boolean true or false"):
        load_config(path)


def test_config_rejects_invalid_user_transcript_env_boolean(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(tmp_path, "delivery:\n  mode: off\n  include_user_transcript: false\n")
    monkeypatch.setenv("OPENCLAW_DELIVERY_INCLUDE_USER_TRANSCRIPT", "1")
    with pytest.raises(ValueError, match="must be true or false"):
        load_config(path)


def test_config_rejects_invalid_mode(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(tmp_path, "delivery:\n  mode: broadcast\n  target: ''\n")
    with pytest.raises(ValueError, match="off, mirror"):
        load_config(path)


def test_config_rejects_unknown_target(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(
        tmp_path,
        "delivery:\n  mode: mirror\n  target: missing\ndelivery_targets: {}\n",
    )
    with pytest.raises(ValueError, match="unknown target"):
        load_config(path)


def test_config_rejects_illegal_channel(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(
        tmp_path,
        "delivery:\n  mode: off\n  target: ''\n"
        "delivery_targets:\n  dashboard:\n    channel: webchat\n    account_id: default\n    to: dashboard\n",
    )
    with pytest.raises(ValueError, match="supported external channel"):
        load_config(path)


def test_config_rejects_empty_delivery_to(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(
        tmp_path,
        "delivery:\n  mode: off\n  target: ''\n"
        "delivery_targets:\n"
        "  discord_home:\n"
        "    channel: discord\n"
        "    account_id: default\n"
        "    to: ''\n",
    )
    with pytest.raises(ValueError, match="delivery_targets.discord_home.to must not be empty"):
        load_config(path)


def test_config_rejects_channel_credentials_in_target(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(
        tmp_path,
        "delivery:\n  mode: off\n  target: ''\n"
        "delivery_targets:\n"
        "  feishu_jie:\n"
        "    channel: feishu\n"
        "    account_id: default\n"
        "    to: user:ou_demo\n"
        "    app_secret: must_not_live_here\n",
    )
    with pytest.raises(ValueError, match="must not contain channel credentials"):
        load_config(path)


def test_config_accepts_gateway_target_shapes(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(
        tmp_path,
        "delivery:\n  mode: mirror\n  target: discord_home\n"
        "delivery_targets:\n"
        "  feishu_jie:\n"
        "    channel: feishu\n"
        "    account_id: default\n"
        "    to: user:ou_demo\n"
        "  discord_home:\n"
        "    channel: discord\n"
        "    account_id: default\n"
        "    to: channel:123456789012345678\n",
    )

    config = load_config(path)

    assert config.delivery_targets["feishu_jie"].to == "user:ou_demo"
    assert config.delivery_targets["discord_home"].to == "channel:123456789012345678"


def test_config_rejects_ambiguous_discord_numeric_target(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(
        tmp_path,
        "delivery:\n  mode: off\n  target: ''\n"
        "delivery_targets:\n"
        "  discord_home:\n"
        "    channel: discord\n"
        "    account_id: default\n"
        "    to: '123456789012345678'\n",
    )
    with pytest.raises(ValueError, match="valid Discord target"):
        load_config(path)


def test_config_env_overrides_preconfigured_target_only(tmp_path, monkeypatch) -> None:
    _clear_delivery_env(monkeypatch)
    path = _write_config(
        tmp_path,
        "delivery:\n  mode: off\n  target: ''\n"
        "delivery_targets:\n"
        "  feishu_jie:\n"
        "    channel: feishu\n"
        "    account_id: default\n"
        "    to: user:ou_yaml\n",
    )
    monkeypatch.setenv("OPENCLAW_DELIVERY_MODE", "mirror")
    monkeypatch.setenv("OPENCLAW_DELIVERY_TARGET", "feishu_jie")
    monkeypatch.setenv("OPENCLAW_DELIVERY_TARGET_FEISHU_JIE_TO", "user:ou_env")

    config = load_config(path)

    assert config.delivery == DeliveryConfig(
        mode="mirror",
        target="feishu_jie",
        include_user_transcript=False,
    )
    assert config.delivery_targets["feishu_jie"].to == "user:ou_env"
