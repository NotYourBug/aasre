"""Current-chat identity gates must precede content access."""

import traceback
from types import SimpleNamespace
from typing import Any

import pytest
from lark_oapi.api.im.v1 import GetMessageRequest, GetMessageResponse
from lark_oapi.api.im.v1.resource.message import Message

from config.constants.feishu import (
    FEISHU_AUTHORIZATION_ERROR_CODES,
    FEISHU_MESSAGE_READ_UNAVAILABLE_ERROR_CODES,
    FEISHU_RATE_LIMIT_ERROR_CODES,
)
from integrations.feishu import message_read
from integrations.feishu.message_read import read_current_message
from integrations.feishu.read_scope import resolve_read_scope
from integrations.feishu.read_types import FeishuReadError, FeishuReadErrorCode, FeishuReadScope
from tests.integrations.feishu_read_support import install_read_transport, message_payload
from tests.integrations.test_feishu_read_scope import valid_view


def _read(cancel_requested: Any = None, **overrides: Any) -> Any:
    def not_cancelled() -> bool:
        return False

    arguments = {
        "app_id": "cli_test",
        "app_secret": "fake-secret",
        "message_id": "om_known",
        "scope": FeishuReadScope("cli_test", "oc_current"),
        "cancel_requested": cancel_requested or not_cancelled,
    }
    arguments.update(overrides)
    return read_current_message(**arguments)


def test_sdk_get_uses_original_card_format_and_tenant_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(_request: GetMessageRequest) -> dict[str, Any]:
        return message_payload()

    probe = install_read_transport(monkeypatch, respond)
    message = _read()
    assert len(probe.requests) == 1
    assert probe.requests[0].card_msg_content_type == "user_card_content"
    assert probe.requests[0].user_id_type == "open_id"
    assert probe.timeouts == [10.0] and probe.token_timeouts == [10.0]
    assert probe.app_ids == ["cli_test"]
    assert (message.message_id, message.chat_id, message.msg_type) == (
        "om_known",
        "oc_current",
        "interactive",
    )
    assert message.content.body_content == '{"schema":"2.0","body":{"elements":[]}}'
    with pytest.raises(FeishuReadError) as rejected:
        _read(app_id="cli_changed")
    assert rejected.value.code is FeishuReadErrorCode.AUTHORIZATION
    assert len(probe.requests) == 1


def test_foreign_or_unverifiable_message_never_touches_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched: list[str] = []
    requests: list[GetMessageRequest] = []
    items: Any = None

    class GuardedItem:
        message_id: Any = "om_known"
        chat_id: Any = "oc_current"
        deleted: Any = False
        msg_type: Any = "text"

        @property
        def body(self) -> Any:
            touched.append("body")
            raise AssertionError("FOREIGN-BODY-CANARY")

    def response(_self: Message, request: GetMessageRequest) -> GetMessageResponse:
        requests.append(request)
        result = GetMessageResponse({"code": 0})
        result.data = SimpleNamespace(items=items)
        return result

    def normalize(_raw: str) -> Any:
        touched.append("normalize")
        raise AssertionError("normalizer must remain untouched")

    monkeypatch.setattr(Message, "get", response)
    monkeypatch.setattr(message_read, "normalize_read_content", normalize, raising=False)
    cases: list[Any] = [None, (), []]
    for overrides in (
        {"chat_id": "oc_foreign"},
        {"message_id": "om_foreign"},
        {"chat_id": None},
        {"deleted": None},
        {"deleted": True},
        {"deleted": 0},
        {"msg_type": "merge_forward"},
    ):
        item = GuardedItem()
        for key, value in overrides.items():
            setattr(item, key, value)
        cases.append([item])
    cases.append([GuardedItem(), GuardedItem()])
    for supplied_items in cases:
        items = supplied_items
        before = len(requests)
        with pytest.raises(FeishuReadError) as rejected:
            _read()
        assert rejected.value.code in (
            FeishuReadErrorCode.MESSAGE_UNAVAILABLE,
            FeishuReadErrorCode.UNSUPPORTED_CONTENT,
        )
        assert len(requests) == before + 1
        assert not touched
        assert "FOREIGN" not in "".join(traceback.format_exception(rejected.value))


def test_upstream_failure_does_not_leak_canary_or_retry(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    code = 0

    def respond(_request: GetMessageRequest) -> dict[str, Any]:
        if code == -1:
            raise RuntimeError("PROVIDER-CANARY oc_foreign")
        return {"code": code, "msg": "PROVIDER-CANARY oc_foreign", "data": {"items": []}}

    probe = install_read_transport(monkeypatch, respond)
    cases = (
        (next(iter(FEISHU_AUTHORIZATION_ERROR_CODES)), FeishuReadErrorCode.MESSAGE_UNAVAILABLE),
        *(
            (unavailable, FeishuReadErrorCode.MESSAGE_UNAVAILABLE)
            for unavailable in FEISHU_MESSAGE_READ_UNAVAILABLE_ERROR_CODES
        ),
        (next(iter(FEISHU_RATE_LIMIT_ERROR_CODES)), FeishuReadErrorCode.RATE_LIMITED),
        (123456, FeishuReadErrorCode.UPSTREAM_ERROR),
        (-1, FeishuReadErrorCode.UPSTREAM_ERROR),
    )
    for supplied_code, expected in cases:
        code = supplied_code
        before = len(probe.requests)
        with pytest.raises(FeishuReadError) as rejected:
            _read()
        assert rejected.value.code is expected
        assert rejected.value.__cause__ is None and rejected.value.__context__ is None
        assert len(probe.requests) == before + 1
        assert (
            "PROVIDER-CANARY"
            not in "".join(traceback.format_exception(rejected.value)) + caplog.text
        )
        assert "oc_foreign" not in repr(rejected.value)


def test_cancellation_discards_completed_get(monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled = True

    def check_cancelled() -> bool:
        return cancelled

    def respond(_request: GetMessageRequest) -> dict[str, Any]:
        nonlocal cancelled
        cancelled = True
        return message_payload(body={"content": "UNRELEASED-CANARY"})

    probe = install_read_transport(monkeypatch, respond)
    for started in (True, False):
        cancelled = started
        with pytest.raises(FeishuReadError) as rejected:
            _read(check_cancelled)
        assert rejected.value.code is FeishuReadErrorCode.CANCELLED
    assert len(probe.requests) == 1


def test_captured_scope_survives_mutation_during_get(monkeypatch: pytest.MonkeyPatch) -> None:
    view = valid_view()
    scope = resolve_read_scope(view)

    def respond(_request: GetMessageRequest) -> dict[str, Any]:
        view["_gateway_chat_id"] = "oc_foreign"
        view["feishu"].app_id = "cli_foreign"
        return message_payload(chat_id="oc_foreign", body={"content": "FOREIGN-CANARY"})

    probe = install_read_transport(monkeypatch, respond)
    with pytest.raises(FeishuReadError) as rejected:
        _read(scope=scope)
    assert rejected.value.code is FeishuReadErrorCode.MESSAGE_UNAVAILABLE
    assert len(probe.requests) == 1
