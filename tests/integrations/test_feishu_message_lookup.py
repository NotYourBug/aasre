"""Reply-parent lookup must expose metadata only."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.feishu import message_lookup


def _stub_lookup(monkeypatch: pytest.MonkeyPatch, items: list[Any]) -> None:
    class _Message:
        def get(self, request: Any) -> Any:
            assert request.message_id == "om_parent"
            data = type("Data", (), {"items": items})()
            return type("Response", (), {"success": lambda _self: True, "data": data})()

    class _Client:
        def __init__(self) -> None:
            self.im = type("Im", (), {"v1": type("V1", (), {"message": _Message()})()})()

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

    monkeypatch.setattr(message_lookup.lark, "Client", _Client)


def _item(*, deleted: bool, chat_id: str = "oc_approved", message_id: str = "om_parent") -> Any:
    class _Item:
        body = "private parent message body"

        def __init__(self) -> None:
            self.message_id = message_id
            self.chat_id = chat_id
            self.deleted = deleted

    return _Item()


def test_wrong_chat_is_rejected_without_exposing_body(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Message:
        def get(self, request: Any) -> Any:
            assert request.message_id == "om_parent"
            item = type(
                "Item",
                (),
                {
                    "message_id": "om_parent",
                    "chat_id": "oc_other",
                    "deleted": False,
                    "body": "never expose this body",
                },
            )()
            data = type("Data", (), {"items": [item]})()
            return type("Response", (), {"success": lambda _self: True, "data": data})()

    class _Client:
        def __init__(self) -> None:
            self.im = type("Im", (), {"v1": type("V1", (), {"message": _Message()})()})()

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

    monkeypatch.setattr(message_lookup.lark, "Client", _Client)

    result = message_lookup.lookup_reply_parent("cli_1", "secret", "om_parent", "oc_approved")

    assert result is None


def test_exact_live_parent_returns_only_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_lookup(monkeypatch, [_item(deleted=False)])

    result = message_lookup.lookup_reply_parent("cli_1", "secret", "om_parent", "oc_approved")

    assert result == message_lookup.FeishuMessageMetadata("om_parent", "oc_approved")
    assert "body" not in vars(result)


@pytest.mark.parametrize(
    "items",
    [
        [],
        [_item(deleted=True)],
        [_item(deleted=False)] * 2,
        [_item(deleted=False, message_id="om_other")],
    ],
)
def test_missing_deleted_multiple_or_wrong_id_parents_fail_closed(
    monkeypatch: pytest.MonkeyPatch, items: list[Any]
) -> None:
    _stub_lookup(monkeypatch, items)

    result = message_lookup.lookup_reply_parent("cli_1", "secret", "om_parent", "oc_approved")

    assert result is None
