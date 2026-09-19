"""Feishu worker dispatch: cancel Event is registered at dispatch time (R19)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.ws.client import Client
from lark_oapi.ws.const import (
    HEADER_MESSAGE_ID,
    HEADER_SEQ,
    HEADER_SUM,
    HEADER_TRACE_ID,
    HEADER_TYPE,
)
from lark_oapi.ws.enum import FrameType, MessageType
from lark_oapi.ws.model import Response
from lark_oapi.ws.pb.pbbp2_pb2 import Frame

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


def _header(frame: Frame, key: str) -> str:
    return str(next(header.value for header in frame.headers if header.key == key))


def _data_frame(message_type: MessageType, payload: dict[str, object]) -> Frame:
    frame = Frame()
    frame.service = 1
    frame.method = FrameType.DATA.value
    frame.SeqID = 0
    frame.LogID = 0
    for key, value in (
        (HEADER_TYPE, message_type.value),
        (HEADER_MESSAGE_ID, "message-1"),
        (HEADER_TRACE_ID, "trace-1"),
        (HEADER_SUM, "1"),
        (HEADER_SEQ, "0"),
    ):
        header = frame.headers.add()
        header.key = key
        header.value = value
    frame.payload = json.dumps(payload).encode()
    return frame


def _p2_payload(event_type: str, event: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": "event-1",
            "event_type": event_type,
            "create_time": "0",
            "token": "",
            "app_id": "app",
            "tenant_key": "tenant",
        },
        "event": event,
    }


def _card_payload_bytes() -> bytes:
    return json.dumps(
        _p2_payload(
            "card.action.trigger",
            {
                "operator": {"open_id": "ou_user-1"},
                "context": {"open_chat_id": "oc_chat-1"},
                "action": {
                    "tag": "button",
                    "value": {"approval_id": "opaque-token"},
                },
            },
        )
    ).encode()


class _CallbackClient:
    response: P2CardActionTriggerResponse | None = None

    def __init__(
        self,
        *_args: object,
        event_handler: EventDispatcherHandler,
        **_kwargs: object,
    ) -> None:
        self._event_handler = event_handler

    def start(self) -> None:
        type(self).response = self._event_handler._do_without_validation(
            _card_payload_bytes()
        )


async def _dispatch_frame(
    dispatcher: EventDispatcherHandler, frame: Frame
) -> None:
    client = worker._ReadyOnConnectClient(
        "app",
        "secret",
        event_handler=dispatcher,
        ready_event=threading.Event(),
        stop_event=threading.Event(),
    )
    try:
        await client._handle_data_frame(frame)
    finally:
        cache_task = client._cache._cron
        cache_task.cancel()
        await asyncio.gather(cache_task, return_exceptions=True)


def test_card_frame_dispatches_callback_and_writes_card_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback_payloads: list[dict[str, object]] = []

    def _on_card(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        assert data.event is not None and data.event.action is not None
        callback_payloads.append(dict(data.event.action.value or {}))
        return P2CardActionTriggerResponse(
            {"toast": {"type": "success", "content": "Approved"}}
        )

    dispatcher = (
        EventDispatcherHandler.builder(encrypt_key="", verification_token="")
        .register_p2_card_action_trigger(_on_card)
        .build()
    )
    written_frames: list[bytes] = []

    async def _record_write(_self: Client, data: bytes) -> None:
        written_frames.append(data)

    monkeypatch.setattr(Client, "_write_message", _record_write)
    expected_payload = {"approval_id": "opaque-token"}
    frame = _data_frame(
        MessageType.CARD,
        _p2_payload(
            "card.action.trigger",
            {
                "operator": {"open_id": REQUESTER},
                "context": {"open_chat_id": CHAT},
                "action": {"tag": "button", "value": expected_payload},
            },
        ),
    )

    asyncio.run(_dispatch_frame(dispatcher, frame))

    assert callback_payloads == [expected_payload]
    assert _header(frame, HEADER_TYPE) == MessageType.CARD.value
    assert len(written_frames) == 1
    ack = Frame()
    ack.ParseFromString(written_frames[0])
    assert _header(ack, HEADER_TYPE) == MessageType.CARD.value
    response = Response(**json.loads(ack.payload.decode()))
    assert response.code == HTTPStatus.OK
    assert response.data


def test_event_frame_still_dispatches_once_through_base_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message_ids: list[str] = []

    def _on_message(data: object) -> None:
        event = data.event  # type: ignore[attr-defined]
        assert event is not None and event.message is not None
        message_ids.append(event.message.message_id)

    dispatcher = (
        EventDispatcherHandler.builder(encrypt_key="", verification_token="")
        .register_p2_im_message_receive_v1(_on_message)
        .build()
    )
    base_types: list[str] = []
    original_handle = Client._handle_data_frame

    async def _record_base(self: Client, frame: Frame) -> None:
        base_types.append(_header(frame, HEADER_TYPE))
        await original_handle(self, frame)

    async def _discard_write(_self: Client, _data: bytes) -> None:
        return None

    monkeypatch.setattr(Client, "_handle_data_frame", _record_base)
    monkeypatch.setattr(Client, "_write_message", _discard_write)
    frame = _data_frame(
        MessageType.EVENT,
        _p2_payload(
            "im.message.receive_v1",
            {
                "sender": {
                    "sender_id": {"open_id": REQUESTER},
                    "sender_type": "user",
                },
                "message": {
                    "message_id": "om-1",
                    "chat_id": CHAT,
                    "message_type": "text",
                    "content": '{"text":"hello"}',
                },
            },
        ),
    )

    asyncio.run(_dispatch_frame(dispatcher, frame))

    assert base_types == [MessageType.EVENT.value]
    assert message_ids == ["om-1"]


def test_gateway_registers_card_callback_and_handles_it_synchronously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handled = MagicMock(
        return_value=P2CardActionTriggerResponse(
            {"toast": {"type": "success", "content": "Approved"}}
        )
    )
    monkeypatch.setattr(worker, "handle_card_action", handled)
    monkeypatch.setattr(worker, "_ReadyOnConnectClient", _CallbackClient)
    _CallbackClient.response = None
    executor = MagicMock(spec=ThreadPoolExecutor)

    worker.run_feishu_gateway_thread(
        settings=FeishuGatewaySettings(
            app_id="app", app_secret="secret", allowed_open_ids=[REQUESTER]
        ),
        logger=LOGGER,
        handler=MagicMock(),
        bindings=MagicMock(),
        executor=executor,
        stop_event=threading.Event(),
        ready_event=threading.Event(),
        output_registry=MagicMock(),
    )

    handled.assert_called_once()
    data = handled.call_args.args[0]
    assert isinstance(data, P2CardActionTrigger)
    assert data.event is not None and data.event.action is not None
    assert data.event.action.value == {"approval_id": "opaque-token"}
    assert _CallbackClient.response is not None
    assert _CallbackClient.response.toast is not None
    assert _CallbackClient.response.toast.content == "Approved"
    executor.submit.assert_not_called()


def test_gateway_card_callback_returns_generic_error_on_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "raw-callback-secret"
    handled = MagicMock(side_effect=RuntimeError(secret))
    test_logger = MagicMock(spec=logging.Logger)
    monkeypatch.setattr(worker, "handle_card_action", handled)
    monkeypatch.setattr(worker, "_ReadyOnConnectClient", _CallbackClient)
    _CallbackClient.response = None

    worker.run_feishu_gateway_thread(
        settings=FeishuGatewaySettings(
            app_id="app", app_secret="secret", allowed_open_ids=[REQUESTER]
        ),
        logger=test_logger,
        handler=MagicMock(),
        bindings=MagicMock(),
        executor=MagicMock(spec=ThreadPoolExecutor),
        stop_event=threading.Event(),
        ready_event=threading.Event(),
        output_registry=MagicMock(),
    )

    response = _CallbackClient.response
    assert response is not None and response.toast is not None
    assert response.toast.type == "error"
    assert response.toast.content == "This interaction could not be completed"
    assert response.card is None
    assert secret not in json.dumps(vars(response.toast))
    test_logger.error.assert_called_once_with(
        "[feishu-gateway] card callback handling failed", exc_info=True
    )


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


def test_inbound_message_preserves_thread_root_for_outbound_replies() -> None:
    sender_id = type("SenderId", (), {"open_id": "ou_user-1"})()
    sender = type("Sender", (), {"sender_id": sender_id})()
    message = type(
        "Message",
        (),
        {
            "message_type": "text",
            "chat_id": "oc_chat-1",
            "message_id": "om_child",
            "root_id": "om_root",
            "parent_id": "om_parent",
            "content": '{"text":"hello"}',
            "mentions": [],
        },
    )()

    inbound = worker.build_inbound_message(sender, message)

    assert inbound is not None
    assert inbound.message_id == "om_child"
    assert inbound.root_id == "om_root"


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
    pending.register_legacy(
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
    assert pending.find_legacy("prompt1") is None


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
    assert pending.find_legacy("prompt1") is not None


def test_another_member_cannot_answer_someone_elses_prompt(monkeypatch) -> None:
    approvals = ApprovalBroker()
    pending = PendingApprovals()
    _pending(pending)
    resolve = MagicMock()
    monkeypatch.setattr(approvals, "resolve", resolve)

    consumed = _resolve(monkeypatch, pending=pending, approvals=approvals, open_id=OUTSIDER)

    assert consumed is True
    resolve.assert_not_called()
    assert pending.find_legacy("prompt1") is not None


def test_reply_that_is_not_a_decision_leaves_the_prompt_open(monkeypatch) -> None:
    approvals = ApprovalBroker()
    pending = PendingApprovals()
    _pending(pending)
    resolve = MagicMock()
    monkeypatch.setattr(approvals, "resolve", resolve)

    consumed = _resolve(monkeypatch, pending=pending, approvals=approvals, text="hold on")

    assert consumed is True
    resolve.assert_not_called()
    assert pending.find_legacy("prompt1") is not None


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
