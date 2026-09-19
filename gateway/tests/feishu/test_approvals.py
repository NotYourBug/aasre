"""Feishu approval cards, callback authority, and lifecycle cleanup."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from gateway.core.middleware.approvals import (
    MAX_APPROVAL_WAIT_SECONDS,
    ApprovalBroker,
    arguments_preview,
)
from gateway.transports.feishu import approvals as approvals_module
from gateway.transports.feishu.approvals import FeishuApprovalPrompter
from gateway.transports.feishu.pending_approvals import ClaimStatus, PendingApprovals

REQUESTER = "ou_user-1"
CHAT = "oc_chat-1"
LOGGER = logging.getLogger("gateway.test")


class _FakeCardClient:
    def __init__(
        self,
        *,
        card_id: str = "card-1",
        message_id: str = "message-1",
        create_error: Exception | None = None,
        send_error: Exception | None = None,
        on_send: Callable[[_FakeCardClient], None] | None = None,
    ) -> None:
        self.card_id = card_id
        self.message_id = message_id
        self.create_error = create_error
        self.send_error = send_error
        self.on_send = on_send
        self.created: list[dict[str, object]] = []
        self.sent: list[tuple[str, str, str]] = []

    def create_card(self, spec: dict[str, object]) -> str:
        if self.create_error is not None:
            raise self.create_error
        self.created.append(spec)
        return self.card_id

    def send_card(self, chat_id: str, card_id: str, *, receive_id_type: str = "chat_id") -> str:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((chat_id, card_id, receive_id_type))
        if self.on_send is not None:
            self.on_send(self)
        return self.message_id


def _card_token(spec: dict[str, object], index: int) -> str:
    payload = json.loads(json.dumps(spec))
    return str(
        payload["body"]["elements"][1]["columns"][index]["elements"][0]["behaviors"][0]["value"][
            "approval_id"
        ]
    )


def _prompter(
    *,
    broker: ApprovalBroker,
    pending: PendingApprovals,
    client: _FakeCardClient,
) -> FeishuApprovalPrompter:
    return FeishuApprovalPrompter(
        broker=broker,
        card_client=client,
        chat_id=CHAT,
        requester_open_id=REQUESTER,
        pending_approvals=pending,
    )


def test_request_registers_before_send_and_retains_settled_tombstone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = ApprovalBroker()
    pending = PendingApprovals()
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", lambda **_kw: True)

    def _click_on_send(client: _FakeCardClient) -> None:
        token = _card_token(client.created[0], 0)
        response = _handle(_callback(token), broker=broker, pending=pending)
        assert response.card is not None

    client = _FakeCardClient(on_send=_click_on_send)
    approved, decided_by = _prompter(broker=broker, pending=pending, client=client).request(
        tool_name="feishu_send_message",
        reason="posting a summary",
        arguments={"api_key": "sk-should-not-appear", "channel": "opensre-test"},
        expiry_seconds=30,
    )

    assert (approved, decided_by) == (True, REQUESTER)
    assert client.sent == [(CHAT, "card-1", "chat_id")]
    assert "sk-should-not-appear" not in json.dumps(client.created[0])
    assert "opensre-test" in json.dumps(client.created[0])
    approve_token = _card_token(client.created[0], 0)
    deny_token = _card_token(client.created[0], 1)
    assert approve_token != deny_token
    duplicate = pending.claim(deny_token, open_id=REQUESTER, chat_id=CHAT)
    assert duplicate.status is ClaimStatus.SETTLED
    assert duplicate.approved is True


def test_request_caps_timeout_and_expires_open_registry_entry() -> None:
    broker = ApprovalBroker()
    pending = PendingApprovals()
    client = _FakeCardClient()
    observed: list[float] = []
    original_wait = broker.wait

    def _wait_without_delay(approval_id: str, *, timeout: float) -> tuple[bool, str]:
        observed.append(timeout)
        return original_wait(approval_id, timeout=0.0)

    broker.wait = _wait_without_delay  # type: ignore[method-assign]

    result = _prompter(broker=broker, pending=pending, client=client).request(
        tool_name="write_tool",
        reason="",
        arguments={},
        expiry_seconds=MAX_APPROVAL_WAIT_SECONDS * 2,
    )

    assert result == (False, "")
    assert observed == [MAX_APPROVAL_WAIT_SECONDS]
    assert pending.drain() == []
    assert broker.close() == 0


@pytest.mark.parametrize(
    "failure",
    ["builder", "create", "empty_card", "register", "send", "empty_message"],
)
def test_request_exposure_failure_abandons_broker_and_registry(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    broker = ApprovalBroker()
    pending = PendingApprovals()
    client = _FakeCardClient(
        card_id="" if failure == "empty_card" else "card-1",
        message_id="" if failure == "empty_message" else "message-1",
        create_error=RuntimeError("boom") if failure == "create" else None,
        send_error=RuntimeError("boom") if failure == "send" else None,
    )

    if failure == "builder":

        def _raise_builder(**_kwargs: object) -> dict[str, object]:
            raise RuntimeError("boom")

        monkeypatch.setattr(approvals_module, "render_approval_prompt_card", _raise_builder)
    if failure == "register":

        def _raise_register(**_kwargs: object) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(pending, "register", _raise_register)

    result = _prompter(broker=broker, pending=pending, client=client).request(
        tool_name="write_tool", reason="", arguments={}, expiry_seconds=30
    )

    assert result == (False, "")
    assert pending.drain() == []
    assert broker.close() == 0


def test_arguments_preview_redacts_secrets() -> None:
    preview = arguments_preview({"api_key": "sk-secret-token", "channel": "opensre"})

    assert "sk-secret-token" not in preview
    assert "opensre" in preview


def test_arguments_preview_is_empty_for_no_arguments() -> None:
    assert arguments_preview({}) == ""


def _callback(
    token: object = "token-approve",
    *,
    open_id: str = REQUESTER,
    chat_id: str = CHAT,
    tag: str = "button",
    value: object | None = None,
) -> P2CardActionTrigger:
    action_value = {"approval_id": token} if value is None else value
    return P2CardActionTrigger(
        {
            "event": {
                "operator": {"open_id": open_id},
                "context": {"open_chat_id": chat_id},
                "action": {"tag": tag, "value": action_value},
            }
        }
    )


def _callback_with_raw_value(value: object) -> P2CardActionTrigger:
    callback = _callback()
    assert callback.event is not None and callback.event.action is not None
    callback.event.action.value = value  # type: ignore[assignment]
    return callback


def _live_request(
    *, expires_at: float = float("inf")
) -> tuple[ApprovalBroker, PendingApprovals, str]:
    broker = ApprovalBroker()
    approval_id = broker.create(platform="feishu", chat_id=CHAT)
    pending = PendingApprovals()
    pending.register(
        broker_approval_id=approval_id,
        approve_token="token-approve",
        deny_token="token-deny",
        requester_open_id=REQUESTER,
        chat_id=CHAT,
        tool_name="write_tool",
        expires_at=expires_at,
    )
    return broker, pending, approval_id


def _handle(
    data: P2CardActionTrigger,
    *,
    broker: ApprovalBroker,
    pending: PendingApprovals,
) -> P2CardActionTriggerResponse:
    return approvals_module.handle_card_action(
        data,
        broker=broker,
        pending_approvals=pending,
        env_allowed_open_ids=[REQUESTER],
        logger=LOGGER,
    )


def test_card_action_authorized_click_resolves_and_returns_raw_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker, pending, approval_id = _live_request()
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", lambda **_kw: True)

    response = _handle(_callback(), broker=broker, pending=pending)

    assert broker.wait(approval_id, timeout=0.0) == (True, REQUESTER)
    assert response.toast is not None and response.toast.content == "Approved"
    assert response.card is not None and response.card.type == "raw"
    assert "button" not in json.dumps(response.card.data)


def test_card_action_settled_sibling_is_private_duplicate_without_second_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker, pending, approval_id = _live_request()
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", lambda **_kw: True)
    resolve = MagicMock(wraps=broker.resolve)
    monkeypatch.setattr(broker, "resolve", resolve)

    first = _handle(_callback(), broker=broker, pending=pending)
    duplicate = _handle(_callback("token-deny"), broker=broker, pending=pending)

    assert first.card is not None
    assert duplicate.toast is not None
    assert duplicate.toast.content == "Already approved"
    assert duplicate.card is None
    resolve.assert_called_once_with(approval_id, approved=True, decided_by=REQUESTER)


def test_card_action_unauthorized_does_not_consume_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker, pending, approval_id = _live_request()
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", lambda **_kw: False)

    rejected = _handle(_callback(), broker=broker, pending=pending)

    assert rejected.toast is not None
    assert rejected.toast.content == "This approval is unavailable"
    assert rejected.card is None
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", lambda **_kw: True)
    _handle(_callback(), broker=broker, pending=pending)
    assert broker.wait(approval_id, timeout=0.0) == (True, REQUESTER)


@pytest.mark.parametrize(
    ("callback"),
    [
        _callback(open_id="ou_other"),
        _callback(chat_id="oc_other"),
        _callback("unknown-token"),
    ],
)
def test_card_action_authority_failure_is_unavailable_without_mutation(
    monkeypatch: pytest.MonkeyPatch, callback: P2CardActionTrigger
) -> None:
    broker, pending, _approval_id = _live_request()
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", lambda **_kw: True)

    response = _handle(callback, broker=broker, pending=pending)

    assert response.toast is not None
    assert response.toast.content == "This approval is unavailable"
    assert response.card is None
    assert broker.close() == 1


def test_card_action_expired_token_is_unavailable_without_broker_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker, pending, _approval_id = _live_request(expires_at=0.0)
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", lambda **_kw: True)

    response = _handle(_callback(), broker=broker, pending=pending)

    assert response.toast is not None
    assert response.toast.content == "This approval is unavailable"
    assert response.card is None
    assert broker.close() == 1


@pytest.mark.parametrize(
    "callback",
    [
        P2CardActionTrigger({}),
        P2CardActionTrigger({"event": {}}),
        _callback(tag="select_static"),
        _callback_with_raw_value("not-a-mapping"),
        _callback(value={"approval_id": "token-approve", "approved": True}),
        _callback(value={"approval_id": ""}),
        _callback(value={"approval_id": ["token-approve"]}),
    ],
)
def test_card_action_malformed_callback_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, callback: P2CardActionTrigger
) -> None:
    broker, pending, _approval_id = _live_request()
    authorized = MagicMock(return_value=True)
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", authorized)

    response = _handle(callback, broker=broker, pending=pending)

    assert response.toast is not None
    assert response.toast.content == "This approval is unavailable"
    assert response.card is None
    authorized.assert_not_called()
    assert broker.close() == 1


def test_card_action_without_s5a_key_returns_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker, pending, _approval_id = _live_request()
    authorized = MagicMock(return_value=True)
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", authorized)

    response = _handle(_callback(value={"other": "action"}), broker=broker, pending=pending)

    assert response.toast is None and response.card is None
    authorized.assert_not_called()
    assert broker.close() == 1


def test_card_action_in_progress_is_unavailable_without_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker, pending, _approval_id = _live_request()
    pending.claim("token-approve", open_id=REQUESTER, chat_id=CHAT)
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", lambda **_kw: True)
    resolve = MagicMock(wraps=broker.resolve)
    monkeypatch.setattr(broker, "resolve", resolve)

    response = _handle(_callback("token-deny"), broker=broker, pending=pending)

    assert response.toast is not None
    assert response.toast.content == "This approval is unavailable"
    assert response.card is None
    resolve.assert_not_called()
    assert broker.close() == 1


def test_card_action_broker_race_does_not_settle_or_return_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker, pending, _approval_id = _live_request()
    monkeypatch.setattr(approvals_module, "is_open_id_authorized", lambda **_kw: True)
    resolve = MagicMock(return_value=False)
    monkeypatch.setattr(broker, "resolve", resolve)

    response = _handle(_callback(), broker=broker, pending=pending)

    assert response.toast is not None
    assert response.toast.content == "This approval is unavailable"
    assert response.card is None
    resolve.assert_called_once()
    assert pending.claim("token-deny", open_id=REQUESTER, chat_id=CHAT).status is (
        ClaimStatus.UNAVAILABLE
    )
