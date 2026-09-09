"""Tests for the Feishu turn output."""

from __future__ import annotations

from typing import Any

import pytest

from gateway.transports.feishu.turn_output import FeishuTurnOutput, _send_text


def test_finalize_sends_text(monkeypatch):
    sent: list[str] = []

    def _fake_send(_app_id: str, _app_secret: str, _chat_id: str, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr("gateway.transports.feishu.turn_output._send_text", _fake_send)
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")
    out.finalize("hello world")
    assert sent == ["hello world"]


def test_finalize_blank_answer_does_not_send(monkeypatch):
    calls: list[str] = []

    def _fake_send(_app_id: str, _app_secret: str, _chat_id: str, text: str) -> None:
        calls.append(text)

    monkeypatch.setattr("gateway.transports.feishu.turn_output._send_text", _fake_send)
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")
    out.finalize("")
    out.finalize("   ")
    assert calls == []


def _stub_lark(monkeypatch: pytest.MonkeyPatch, *, create_impl: Any) -> None:
    """Replace the lark SDK with a stub whose ``message.create`` is *create_impl*."""

    class _Message:
        def create(self, request: Any) -> Any:
            return create_impl(request)

    class _V1:
        def __init__(self) -> None:
            self.message = _Message()

    class _Im:
        def __init__(self) -> None:
            self.v1 = _V1()

    class _Client:
        def __init__(self) -> None:
            self.im = _Im()

        @staticmethod
        def builder() -> Any:
            return _Builder()

    class _Builder:
        def app_id(self, _value: str) -> _Builder:
            return self

        def app_secret(self, _value: str) -> _Builder:
            return self

        def build(self) -> _Client:
            return _Client()

    class _Lark:
        Client = _Client

    monkeypatch.setattr("gateway.transports.feishu.turn_output.lark", _Lark)


def _response(*, ok: bool, msg: str = "success", message_id: str = "om_1") -> Any:
    data = type("D", (), {"message_id": message_id})()
    return type(
        "R", (), {"success": lambda _self: ok, "code": 0 if ok else 999, "msg": msg, "data": data}
    )()


def test_send_text_returns_message_id_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_lark(monkeypatch, create_impl=lambda _req: _response(ok=True))

    message_id = _send_text("cli_x", "s_secret_123", "oc_chat", "hello")

    assert message_id == "om_1"


def test_send_text_raises_on_business_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_lark(monkeypatch, create_impl=lambda _req: _response(ok=False, msg="bot not in chat"))

    with pytest.raises(RuntimeError, match="bot not in chat"):
        _send_text("cli_x", "s_secret_123", "oc_chat", "hello")
