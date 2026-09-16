"""The cardkit client: request shapes, error surfacing, client reuse."""

from __future__ import annotations

import json
from typing import Any

import pytest

from integrations.feishu.card_client import FeishuCardClient, FeishuStreamRejected


def _stub_lark(
    monkeypatch: pytest.MonkeyPatch, module: Any, *, impl: Any, calls: list[Any]
) -> None:
    """Replace the lark SDK with a stub whose cardkit tree records requests."""

    class _Card:
        def create(self, request: Any) -> Any:
            calls.append(("card.create", request))
            return impl(request)

        def settings(self, request: Any) -> Any:
            calls.append(("card.settings", request))
            return impl(request)

    class _Element:
        def content(self, request: Any) -> Any:
            calls.append(("element.content", request))
            return impl(request)

    class _V1:
        def __init__(self) -> None:
            self.card = _Card()
            self.card_element = _Element()

    class _Cardkit:
        def __init__(self) -> None:
            self.v1 = _V1()

    class _Message:
        def create(self, request: Any) -> Any:
            calls.append(("message.create", request))
            return impl(request)

    class _ImV1:
        def __init__(self) -> None:
            self.message = _Message()

    class _Im:
        def __init__(self) -> None:
            self.v1 = _ImV1()

    class _Client:
        def __init__(self) -> None:
            self.cardkit = _Cardkit()
            self.im = _Im()

        @staticmethod
        def builder() -> Any:
            return _Builder()

    class _Builder:
        def app_id(self, _v: str) -> Any:
            return self

        def app_secret(self, _v: str) -> Any:
            return self

        def build(self) -> _Client:
            return _Client()

    class _Lark:
        Client = _Client

    monkeypatch.setattr("integrations.feishu.card_client.lark", _Lark)


def _ok(**data: Any) -> Any:
    return type(
        "R",
        (),
        {"success": lambda _self: True, "code": 0, "msg": "ok", "data": type("D", (), data)()},
    )()


def _err(code: int) -> Any:
    return type(
        "R",
        (),
        {"success": lambda _self: False, "code": code, "msg": f"err {code}", "data": None},
    )()


def test_create_card_sends_card_json_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wire ``type`` is ``card_json``; ``card`` is the message-reference form."""
    import integrations.feishu.card_client as module

    calls: list[Any] = []
    _stub_lark(monkeypatch, module, impl=lambda _r: _ok(card_id="c_1"), calls=calls)

    client = FeishuCardClient("cli_x", "s_1")
    card_id = client.create_card({"schema": "2.0"})

    assert card_id == "c_1"
    body = calls[0][1].request_body
    assert body.type == "card_json"
    assert json.loads(body.data) == {"schema": "2.0"}


def test_update_element_sends_full_text_and_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    import integrations.feishu.card_client as module

    calls: list[Any] = []
    _stub_lark(monkeypatch, module, impl=lambda _r: _ok(), calls=calls)

    FeishuCardClient("a", "s").update_element("c_1", "stream_md", "full text", 7)

    request = calls[0][1]
    assert request.card_id == "c_1"
    assert request.element_id == "stream_md"
    assert request.request_body.content == "full text"
    assert request.request_body.sequence == 7


def test_element_updates_carry_a_fresh_uuid_unless_one_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry of the same logical update must resend that update's uuid (D8)."""
    import integrations.feishu.card_client as module

    calls: list[Any] = []
    _stub_lark(monkeypatch, module, impl=lambda _r: _ok(), calls=calls)
    client = FeishuCardClient("a", "s")

    client.update_element("c_1", "stream_md", "x", 1)
    client.update_element("c_1", "stream_md", "xx", 2)
    client.update_element("c_1", "stream_md", "xx", 2, uuid="fixed")

    first, second, third = (call[1].request_body.uuid for call in calls)
    assert first and second, "every update must carry an idempotency uuid"
    assert first != second, "a new logical update must not reuse the previous uuid"
    assert third == "fixed", "a retry must be able to pin the uuid it first sent"


def test_close_streaming_carries_a_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    import integrations.feishu.card_client as module

    calls: list[Any] = []
    _stub_lark(monkeypatch, module, impl=lambda _r: _ok(), calls=calls)

    FeishuCardClient("a", "s").close_streaming("c_1", 9, uuid="fixed")

    assert calls[0][1].request_body.uuid == "fixed"


def test_close_streaming_sends_disabled_streaming_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import integrations.feishu.card_client as module

    calls: list[Any] = []
    _stub_lark(monkeypatch, module, impl=lambda _r: _ok(), calls=calls)

    FeishuCardClient("a", "s").close_streaming("c_1", 9)

    settings = json.loads(calls[0][1].request_body.settings)
    assert settings == {"config": {"streaming_mode": False}}
    assert calls[0][1].request_body.sequence == 9


def test_send_card_references_the_card_id(monkeypatch: pytest.MonkeyPatch) -> None:
    import integrations.feishu.card_client as module

    calls: list[Any] = []
    _stub_lark(monkeypatch, module, impl=lambda _r: _ok(message_id="om_1"), calls=calls)

    message_id = FeishuCardClient("a", "s").send_card("oc_chat", "c_1")

    assert message_id == "om_1"
    body = calls[0][1].request_body
    assert body.msg_type == "interactive"
    assert json.loads(body.content) == {"type": "card", "data": {"card_id": "c_1"}}


def test_a_stream_error_code_is_raised_as_feishu_stream_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations.feishu.card_client as module

    calls: list[Any] = []
    _stub_lark(monkeypatch, module, impl=lambda _r: _err(300317), calls=calls)

    with pytest.raises(FeishuStreamRejected) as caught:
        FeishuCardClient("a", "s").update_element("c_1", "stream_md", "x", 3)

    assert caught.value.code == 300317


def test_a_non_stream_error_still_raises_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the three ladder codes are stream failures; others must not be swallowed."""
    import integrations.feishu.card_client as module

    calls: list[Any] = []
    _stub_lark(monkeypatch, module, impl=lambda _r: _err(11310), calls=calls)

    with pytest.raises(RuntimeError, match="11310"):
        FeishuCardClient("a", "s").update_element("c_1", "stream_md", "x", 3)


def test_the_client_is_built_once_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """A streaming session makes many calls; rebuilding the client per call is waste."""
    import integrations.feishu.card_client as module

    built: list[int] = []
    calls: list[Any] = []
    _stub_lark(monkeypatch, module, impl=lambda _r: _ok(card_id="c_1"), calls=calls)

    original = module.lark.Client.builder

    def _counting_builder() -> Any:
        built.append(1)
        return original()

    monkeypatch.setattr(module.lark.Client, "builder", staticmethod(_counting_builder))

    client = FeishuCardClient("a", "s")
    client.create_card({"schema": "2.0"})
    client.create_card({"schema": "2.0"})

    assert len(built) == 1
