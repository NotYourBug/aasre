"""Tests for integrations.feishu.delivery."""

from __future__ import annotations

import json
from typing import Any

import pytest

from integrations.feishu.delivery import post_feishu_message
from integrations.feishu.delivery_types import (
    FeishuDeliveryErrorCategory,
    FeishuMessageSendResult,
    FeishuSendCertainty,
)

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


def _response(*, ok: bool, code: int = 0, msg: str = "success", message_id: str = "om_1") -> Any:
    data = type("D", (), {"message_id": message_id})()
    return type(
        "R",
        (),
        {"success": lambda _self: ok, "code": code, "msg": msg, "data": data},
    )()


def test_post_feishu_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_lark(monkeypatch, create_impl=lambda _req: _response(ok=True))

    result = post_feishu_message(_APP_ID, _APP_SECRET, _RECEIVE_ID, "chat_id", "hello")

    assert result == FeishuMessageSendResult(
        accepted=True,
        message_id="om_1",
        error_category=None,
        certainty=FeishuSendCertainty.CONFIRMED_SENT,
    )
    request = captured[0]
    assert request.receive_id_type == "chat_id"
    assert request.request_body.receive_id == _RECEIVE_ID
    assert request.request_body.msg_type == "text"
    assert request.request_body.content == json.dumps({"text": "hello"})


def test_rejected_text_send_returns_only_fixed_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    body = "# Secret report"
    _stub_lark(
        monkeypatch,
        create_impl=lambda _req: _response(
            ok=False,
            code=230020,
            msg=f"{_APP_SECRET} {body} {_RECEIVE_ID} Authorization: Bearer token",
        ),
    )

    result = post_feishu_message(_APP_ID, _APP_SECRET, _RECEIVE_ID, "chat_id", body)

    assert result.accepted is False
    assert result.error_category is FeishuDeliveryErrorCategory.RATE_LIMIT
    assert result.certainty is FeishuSendCertainty.DEFINITELY_NOT_SENT
    assert result.message_id == ""
    assert all(
        value not in repr(result) for value in (_APP_SECRET, body, _RECEIVE_ID, "Bearer token")
    )


def test_visible_send_exception_is_uncertain_without_raw_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "# Secret report"

    def _raise(_request: Any) -> Any:
        raise ConnectionError(f"{_APP_SECRET} {body} {_RECEIVE_ID} Authorization: Bearer token")

    _stub_lark(monkeypatch, create_impl=_raise)

    result = post_feishu_message(_APP_ID, _APP_SECRET, _RECEIVE_ID, "chat_id", body)

    assert result == FeishuMessageSendResult(
        accepted=False,
        message_id="",
        error_category=FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN,
        certainty=FeishuSendCertainty.MAYBE_SENT,
    )
    assert all(
        value not in repr(result) for value in (_APP_SECRET, body, _RECEIVE_ID, "Bearer token")
    )


def test_send_success_without_message_id_is_uncertain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_lark(
        monkeypatch,
        create_impl=lambda _req: _response(ok=True, message_id=""),
    )

    result = post_feishu_message(_APP_ID, _APP_SECRET, _RECEIVE_ID, "chat_id", "body")

    assert result.error_category is FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN
    assert result.certainty is FeishuSendCertainty.MAYBE_SENT
    assert result.accepted is False


def test_post_feishu_message_contains_sdk_construction_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A builder that raises during client construction must be contained,
    not escape ``post_feishu_message`` (Greptile issue: SDK construction was
    outside the transport exception boundary)."""

    class _BoomBuilder:
        def app_id(self, _value: str) -> _BoomBuilder:
            return self

        def app_secret(self, _value: str) -> _BoomBuilder:
            return self

        def build(self) -> Any:
            raise ValueError(f"bad app secret {_APP_SECRET}")

    class _Client:
        @staticmethod
        def builder() -> _BoomBuilder:
            return _BoomBuilder()

    class _Lark:
        Client = _Client

    monkeypatch.setattr("integrations.feishu.delivery.lark", _Lark)

    result = post_feishu_message(_APP_ID, _APP_SECRET, _RECEIVE_ID, "chat_id", "hello")

    assert result == FeishuMessageSendResult(
        accepted=False,
        message_id="",
        error_category=FeishuDeliveryErrorCategory.TRANSPORT,
        certainty=FeishuSendCertainty.DEFINITELY_NOT_SENT,
    )


