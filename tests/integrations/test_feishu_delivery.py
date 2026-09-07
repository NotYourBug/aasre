"""Tests for integrations.feishu.delivery."""

from __future__ import annotations

import json
from typing import Any

import pytest

from integrations.feishu.delivery import post_feishu_message, send_feishu_report

_APP_ID = "cli_x"
_APP_SECRET = "s_secret_123"
_RECEIVE_ID = "oc_x"


def _stub_lark(monkeypatch: pytest.MonkeyPatch, *, create_impl: Any) -> list[Any]:
    """Replace the lark SDK with a stub whose ``message.create`` is *create_impl*.

    Reproduces the real builder chain so ``post_feishu_message`` runs end to end
    while the captured request can be inspected without hitting the network.
    """
    captured: list[Any] = []

    class _Message:
        def create(self, request: Any) -> Any:
            captured.append(request)
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
        def builder() -> _Builder:
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

    monkeypatch.setattr("integrations.feishu.delivery.lark", _Lark)
    return captured


def _response(*, ok: bool, msg: str = "success", message_id: str = "om_1") -> Any:
    data = type("D", (), {"message_id": message_id})()
    return type("R", (), {"success": lambda _self: ok, "msg": msg, "data": data})()


def test_post_feishu_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_lark(monkeypatch, create_impl=lambda _req: _response(ok=True))

    ok, error, message_id = post_feishu_message(
        _APP_ID, _APP_SECRET, _RECEIVE_ID, "chat_id", "hello"
    )

    assert ok is True
    assert error == ""
    assert message_id == "om_1"
    request = captured[0]
    assert request.receive_id_type == "chat_id"
    assert request.request_body.receive_id == _RECEIVE_ID
    assert request.request_body.msg_type == "text"
    assert request.request_body.content == json.dumps({"text": "hello"})


def test_post_feishu_message_failure_returns_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_lark(monkeypatch, create_impl=lambda _req: _response(ok=False, msg="denied"))

    ok, error, message_id = post_feishu_message(
        _APP_ID, _APP_SECRET, _RECEIVE_ID, "chat_id", "hello"
    )

    assert ok is False
    assert error == "denied"
    assert message_id == ""


def test_post_feishu_message_exception_redacts_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_request: Any) -> Any:
        raise ConnectionError(f"auth failed for {_APP_SECRET}")

    _stub_lark(monkeypatch, create_impl=_raise)

    ok, error, message_id = post_feishu_message(
        _APP_ID, _APP_SECRET, _RECEIVE_ID, "chat_id", "hello"
    )

    assert ok is False
    assert _APP_SECRET not in error
    assert "<redacted>" in error
    assert message_id == ""


def test_send_feishu_report_missing_creds() -> None:
    ok, error = send_feishu_report("report", {})
    assert ok is False
    assert "Missing" in error


def test_send_feishu_report_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.feishu.delivery.post_feishu_message",
        lambda _app_id, _app_secret, _receive_id, _receive_id_type, _text: (True, "", "om_2"),
    )

    ok, error = send_feishu_report(
        "Report text",
        {"app_id": _APP_ID, "app_secret": _APP_SECRET, "receive_id": _RECEIVE_ID},
    )

    assert ok is True
    assert error == ""


def test_send_feishu_report_truncates_to_4096(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def _fake_post(
        app_id: str, app_secret: str, receive_id: str, receive_id_type: str, text: str
    ) -> tuple[bool, str, str]:
        captured["text"] = text
        return (True, "", "om_3")

    monkeypatch.setattr("integrations.feishu.delivery.post_feishu_message", _fake_post)

    send_feishu_report(
        "x" * 5000,
        {"app_id": _APP_ID, "app_secret": _APP_SECRET, "receive_id": _RECEIVE_ID},
    )

    assert len(captured["text"]) == 4096
    assert captured["text"].endswith("…")
