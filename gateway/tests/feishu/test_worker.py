"""Feishu worker dispatch: cancel Event is registered at dispatch time (R19)."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.middleware.conversation_locks import ConversationLockRegistry
from gateway.transports.feishu import worker
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.pending_approvals import PendingApprovals
from gateway.transports.feishu.session_rotation import conversation_key
from gateway.transports.feishu.settings import FeishuGatewaySettings
from gateway.transports.feishu.worker import _dispatch_turn

LOGGER = logging.getLogger("gateway.test")


def test_dispatch_registers_cancel_before_handler_runs() -> None:
    """The cancel Event is published before the dispatched turn body runs."""
    registry = ActiveTurnRegistry()
    inbound = FeishuInboundMessage(
        chat_id="oc_chat-1",
        open_id="ou_user-1",
        message_id="m1",
        text="hello",
    )
    started = threading.Event()
    observed: list[bool] = []

    def spy(_inbound: FeishuInboundMessage, **_kwargs: object) -> None:
        # When the dispatched turn first runs, the cancel Event must already be
        # registered for this conversation key (dispatch-time registration).
        observed.append(registry.request_stop(conversation_key(_inbound)))
        started.set()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            with patch("gateway.transports.feishu.worker.handle_inbound_turn", spy):
                _dispatch_turn(
                    inbound,
                    settings=FeishuGatewaySettings(
                        app_id="app",
                        app_secret="secret",
                        allowed_open_ids=["ou_user-1"],
                    ),
                    session_resolver=MagicMock(),  # type: ignore[arg-type]
                    active_cancels=registry,
                    conversation_locks=ConversationLockRegistry(),
                    approvals=ApprovalBroker(),
                    pending_approvals=PendingApprovals(),
                    send_text=lambda _c, _t: "",
                    handler=lambda *_args: None,
                    logger=LOGGER,
                    executor=executor,
                    loop=loop,
                    turn_slots=threading.BoundedSemaphore(2),
                )
            await asyncio.to_thread(started.wait, 2.0)
            assert observed == [True]
        finally:
            executor.shutdown(wait=True)

    asyncio.run(_run())


def test_dispatch_failure_releases_slot_and_unregisters_cancel() -> None:
    """A synchronous dispatch failure must not leak the slot or the cancel Event."""
    registry = ActiveTurnRegistry()
    inbound = FeishuInboundMessage(
        chat_id="oc_chat-1",
        open_id="ou_user-1",
        message_id="m1",
        text="hello",
    )
    key = conversation_key(inbound)
    slots = threading.BoundedSemaphore(1)
    loop = MagicMock()
    loop.run_in_executor.side_effect = RuntimeError("Event loop is closed")

    _dispatch_turn(
        inbound,
        settings=FeishuGatewaySettings(
            app_id="app",
            app_secret="secret",
            allowed_open_ids=["ou_user-1"],
        ),
        session_resolver=MagicMock(),  # type: ignore[arg-type]
        active_cancels=registry,
        conversation_locks=ConversationLockRegistry(),
        approvals=ApprovalBroker(),
        pending_approvals=PendingApprovals(),
        send_text=lambda _c, _t: "",
        handler=lambda *_args: None,
        logger=LOGGER,
        executor=ThreadPoolExecutor(max_workers=1),
        loop=loop,  # type: ignore[arg-type]
        turn_slots=slots,
    )

    # The failed dispatch released the single slot and dropped the cancel Event.
    assert slots.acquire(blocking=False) is True
    assert registry.request_stop(key) is False


REQUESTER = "ou_user-1"
OUTSIDER = "ou_user-2"
CHAT = "oc_chat-1"


def _pending(pending: PendingApprovals) -> None:
    pending.register(
        "prompt1", approval_id="approval-id-1", requester_open_id=REQUESTER, chat_id=CHAT
    )


def _resolve(
    monkeypatch,
    *,
    pending: PendingApprovals,
    approvals: ApprovalBroker,
    open_id: str = REQUESTER,
    chat_id: str = CHAT,
    text: str = "approve",
) -> bool:
    monkeypatch.setattr(worker, "is_open_id_authorized", lambda **_kw: True)
    return worker._resolve_approval_reply(
        parent_id="prompt1",
        open_id=open_id,
        chat_id=chat_id,
        text=text,
        approvals=approvals,
        pending_approvals=pending,
        env_allowed_open_ids=[REQUESTER],
        logger=LOGGER,
    )


def test_approval_reply_resolves_broker_instead_of_dispatching(monkeypatch) -> None:
    approvals = ApprovalBroker()
    pending = PendingApprovals()
    _pending(pending)
    resolve = MagicMock()
    monkeypatch.setattr(approvals, "resolve", resolve)

    consumed = _resolve(monkeypatch, pending=pending, approvals=approvals)

    assert consumed is True
    resolve.assert_called_once_with("approval-id-1", approved=True, decided_by=REQUESTER)
    assert pending.find("prompt1") is None


def test_approval_reply_deny_resolves_denied(monkeypatch) -> None:
    approvals = ApprovalBroker()
    pending = PendingApprovals()
    _pending(pending)
    resolve = MagicMock()
    monkeypatch.setattr(approvals, "resolve", resolve)

    consumed = _resolve(monkeypatch, pending=pending, approvals=approvals, text="deny")

    assert consumed is True
    resolve.assert_called_once_with("approval-id-1", approved=False, decided_by=REQUESTER)


def test_unauthorized_reply_does_not_resolve(monkeypatch) -> None:
    approvals = ApprovalBroker()
    pending = PendingApprovals()
    _pending(pending)
    monkeypatch.setattr(worker, "is_open_id_authorized", lambda **_kw: False)

    consumed = worker._resolve_approval_reply(
        parent_id="prompt1",
        open_id=OUTSIDER,
        chat_id=CHAT,
        text="approve",
        approvals=approvals,
        pending_approvals=pending,
        env_allowed_open_ids=[REQUESTER],
        logger=LOGGER,
    )

    assert consumed is True
    assert pending.find("prompt1") is not None


def test_another_member_cannot_answer_someone_elses_prompt(monkeypatch) -> None:
    approvals = ApprovalBroker()
    pending = PendingApprovals()
    _pending(pending)
    resolve = MagicMock()
    monkeypatch.setattr(approvals, "resolve", resolve)

    consumed = _resolve(monkeypatch, pending=pending, approvals=approvals, open_id=OUTSIDER)

    assert consumed is True
    resolve.assert_not_called()
    assert pending.find("prompt1") is not None


def test_reply_that_is_not_a_decision_leaves_the_prompt_open(monkeypatch) -> None:
    approvals = ApprovalBroker()
    pending = PendingApprovals()
    _pending(pending)
    resolve = MagicMock()
    monkeypatch.setattr(approvals, "resolve", resolve)

    consumed = _resolve(monkeypatch, pending=pending, approvals=approvals, text="hold on")

    assert consumed is True
    resolve.assert_not_called()
    assert pending.find("prompt1") is not None


def test_non_reply_parent_id_falls_through_to_dispatch() -> None:
    approvals = ApprovalBroker()
    pending = PendingApprovals()

    consumed = worker._resolve_approval_reply(
        parent_id="prompt1",
        open_id=REQUESTER,
        chat_id=CHAT,
        text="approve",
        approvals=approvals,
        pending_approvals=pending,
        env_allowed_open_ids=[REQUESTER],
        logger=LOGGER,
    )

    assert consumed is False
