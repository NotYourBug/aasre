"""Feishu Gateway WebSocket worker (lark-oapi)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.core.token import TokenManager
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.ws.client import Client
from lark_oapi.ws.client import loop as _ws_loop
from lark_oapi.ws.const import HEADER_MESSAGE_ID, HEADER_TYPE
from lark_oapi.ws.enum import MessageType
from lark_oapi.ws.pb.pbbp2_pb2 import Frame

from config.constants.gateway import NO_ACTIVE_TURN_MESSAGE
from gateway.core.middleware.active_turns import ActiveTurnRegistry, is_stop_command
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.middleware.conversation_locks import ConversationLockRegistry
from gateway.core.storage import SessionResolver
from gateway.core.storage.session.binding_store import BindingStore
from gateway.transports.feishu.card_actions import handle_card_action
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.feedback import FeishuFeedbackService
from gateway.transports.feishu.inbound_handler import _run_turn as handle_inbound_turn
from gateway.transports.feishu.pending_approvals import PendingApprovals
from gateway.transports.feishu.reaction_lifecycle import ReactionLifecycleManager
from gateway.transports.feishu.session_rotation import conversation_key
from gateway.transports.feishu.settings import FeishuGatewaySettings
from gateway.transports.feishu.turn_output import FeishuTurnOutputRegistry, _send_text
from infrastructure.turn_host.turn_callback import TurnCallback
from integrations.feishu import TRACKED_MESSAGE_TYPES, flatten_post, resource_refs

_PLATFORM_FEISHU = "feishu"


def strip_leading_feishu_mentions(text: str, bot_mention_keys: frozenset[str]) -> str:
    """Remove leading tokens that are the bot's own ``@_user_N`` mention keys.

    Only the bot's mention placeholder is stripped; other members' mentions and
    a literal ``@all``/``@everyone`` are preserved, so command/approval routing
    reads the text the user actually typed. ``@bot /new`` routes as ``/new``.
    """
    stripped = text.strip()
    if not bot_mention_keys:
        return stripped
    while stripped:
        matched = False
        for key in sorted(bot_mention_keys, key=len, reverse=True):
            if stripped == key or stripped.startswith(key + " "):
                stripped = stripped[len(key) :].strip()
                matched = True
                break
        if not matched:
            break
    return stripped


def build_inbound_message(sender: Any, message: Any) -> FeishuInboundMessage | None:
    """Normalize one raw ``im.message.receive_v1`` message into a turn, or ``None``.

    ``None`` means the message earns no turn at all: an unhandled message type
    (a sticker, a system notice), or a payload missing the chat, the sender, or
    both the text and the attachments that would give the agent something to
    read. Nothing is sent back to the user in that case.
    """
    message_type = message.message_type or ""
    if message_type not in TRACKED_MESSAGE_TYPES:
        return None
    chat_id = message.chat_id or ""
    sender_id = sender.sender_id
    open_id = (sender_id.open_id or "") if sender_id is not None else ""
    if not chat_id or not open_id:
        return None
    content = _message_content(message)
    bot_mention_keys = frozenset(
        mention.key
        for mention in (message.mentions or [])
        if mention.mentioned_type == "bot" and mention.key
    )
    if message_type == "text":
        text = strip_leading_feishu_mentions(str(content.get("text", "") or ""), bot_mention_keys)
    elif message_type == "post":
        text = flatten_post(content, bot_keys=bot_mention_keys)
    else:
        text = ""
    attachments = resource_refs(message_type, content)
    if not text and not attachments:
        return None
    return FeishuInboundMessage(
        chat_id=chat_id,
        open_id=open_id,
        message_id=message.message_id or "",
        text=text,
        root_id=getattr(message, "root_id", "") or "",
        parent_id=message.parent_id or "",
        attachments=attachments,
    )


def _message_content(message: Any) -> dict[str, Any]:
    """The message's parsed ``content`` object; empty when it is not a JSON object."""
    try:
        parsed = json.loads(message.content or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _verify_feishu_credentials(app_id: str, app_secret: str) -> None:
    """Fetch the tenant access token, raising ``ObtainAccessTokenException`` on bad creds.

    Uses the same REST ``lark.Client`` builder as :func:`turn_output._send_text`
    so verification exercises the exact credential path turns use.
    """
    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
    TokenManager.get_self_tenant_token(client.config)


def _dispatch_turn(
    inbound: FeishuInboundMessage,
    *,
    settings: FeishuGatewaySettings,
    session_resolver: SessionResolver,
    active_cancels: ActiveTurnRegistry,
    conversation_locks: ConversationLockRegistry,
    approvals: ApprovalBroker,
    pending_approvals: PendingApprovals,
    send_text: Callable[[str, str], str],
    handler: TurnCallback,
    logger: logging.Logger,
    executor: ThreadPoolExecutor,
    loop: asyncio.AbstractEventLoop,
    turn_slots: threading.BoundedSemaphore,
    output_registry: FeishuTurnOutputRegistry | None = None,
    reactions: ReactionLifecycleManager | None = None,
    feedback: FeishuFeedbackService | None = None,
) -> None:
    """Register the cancel Event and submit the turn to the executor.

    The Event is registered *before* ``run_in_executor`` so a ``/stop`` arriving
    after dispatch but before the turn body acquires the conversation lock still
    finds it. Bounded by ``turn_slots`` so the WS loop never queues unboundedly;
    a full slot drops the turn (logged) rather than blocking the loop thread.
    """
    key = conversation_key(inbound)
    turn_cancel = threading.Event()
    active_cancels.register(key, turn_cancel)

    if not turn_slots.acquire(blocking=False):
        logger.warning(
            "[feishu-gateway] turn dropped: concurrency limit reached chat=%s",
            inbound.chat_id,
        )
        active_cancels.unregister(key, turn_cancel)
        return

    _run_turn = partial(
        handle_inbound_turn,
        inbound,
        settings=settings,
        session_resolver=session_resolver,
        active_cancels=active_cancels,
        conversation_locks=conversation_locks,
        approvals=approvals,
        pending_approvals=pending_approvals,
        send_text=send_text,
        handler=handler,
        logger=logger,
        turn_cancel=turn_cancel,
        output_registry=output_registry,
        reactions=reactions,
        feedback=feedback,
    )

    def _on_turn_done(future: asyncio.Future[None]) -> None:
        turn_slots.release()
        active_cancels.unregister(key, turn_cancel)
        try:
            future.result()
        except Exception:
            logger.error("[feishu-gateway] turn dispatch failed", exc_info=True)

    try:
        future = loop.run_in_executor(executor, _run_turn)
    except Exception:
        # A synchronous dispatch failure (the WS loop closed during shutdown)
        # never reaches ``_on_turn_done``, so release the slot and drop the
        # cancel Event here instead of leaking both on a daemon thread.
        turn_slots.release()
        active_cancels.unregister(key, turn_cancel)
        logger.error(
            "[feishu-gateway] turn dispatch rejected: executor unavailable",
            exc_info=True,
        )
        return
    future.add_done_callback(_on_turn_done)


class _ReadyOnConnectClient(Client):
    """lark WS client that signals readiness once connected and stops on demand.

    ``lark_oapi.ws.Client`` has no first-connect callback (``on_reconnected``
    fires on reconnect only), so setting ``ready_event`` before ``start()``
    could report a dead worker as connected if the initial handshake failed.
    Overriding ``_connect`` sets the event only after the socket is established;
    an auth/config failure raises ``ClientException`` and never signals ready,
    so startup fails closed instead.

    ``Client.start()`` also blocks forever in ``run_until_complete(_select())``
    (an infinite sleep) and exposes no stop method, so a stop can never join the
    worker. A stop watcher scheduled on the SDK's module-level loop calls
    ``loop.stop()`` once ``stop_event`` is set, which unblocks ``_select`` so
    ``start()`` returns and the worker's shutdown (denying pending approvals)
    runs.
    """

    def __init__(
        self,
        *args: object,
        ready_event: threading.Event,
        stop_event: threading.Event,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._ready_event = ready_event
        self._stop_event = stop_event
        self._stopped = False
        self._card_frame_lock = threading.Lock()
        self._adapted_card_message_ids: set[str] = set()

    async def _handle_data_frame(self, frame: Frame) -> None:
        type_header = next((header for header in frame.headers if header.key == HEADER_TYPE), None)
        if type_header is None or type_header.value != MessageType.CARD.value:
            await super()._handle_data_frame(frame)
            return
        message_id = next(
            header.value for header in frame.headers if header.key == HEADER_MESSAGE_ID
        )
        forwarded = Frame()
        forwarded.CopyFrom(frame)
        next(
            header for header in forwarded.headers if header.key == HEADER_TYPE
        ).value = MessageType.EVENT.value
        with self._card_frame_lock:
            self._adapted_card_message_ids.add(message_id)
        try:
            await super()._handle_data_frame(forwarded)
        finally:
            with self._card_frame_lock:
                self._adapted_card_message_ids.discard(message_id)

    async def _write_message(self, data: bytes) -> None:
        frame = Frame()
        try:
            frame.ParseFromString(data)
        except Exception:
            await super()._write_message(data)
            return
        message_id = next(
            (header.value for header in frame.headers if header.key == HEADER_MESSAGE_ID),
            None,
        )
        with self._card_frame_lock:
            restore_card_type = (
                message_id is not None and message_id in self._adapted_card_message_ids
            )
        if restore_card_type:
            type_header = next(
                (header for header in frame.headers if header.key == HEADER_TYPE),
                None,
            )
            if type_header is not None:
                type_header.value = MessageType.CARD.value
                data = frame.SerializeToString()
        await super()._write_message(data)

    async def _connect(self) -> None:
        await super()._connect()
        self._ready_event.set()

    async def _watch_stop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)
        self._stopped = True
        _ws_loop.stop()

    def start(self) -> None:
        _ws_loop.create_task(self._watch_stop())
        try:
            super().start()
        except RuntimeError:
            # ``loop.stop()`` makes ``run_until_complete(_select())`` raise
            # "Event loop stopped before Future completed"; a deliberate stop
            # returns normally, any other RuntimeError still propagates.
            if not self._stopped:
                raise


def run_feishu_gateway_thread(
    *,
    settings: FeishuGatewaySettings,
    logger: logging.Logger,
    handler: TurnCallback,
    bindings: BindingStore,
    executor: ThreadPoolExecutor,
    stop_event: threading.Event,
    ready_event: threading.Event,
    output_registry: FeishuTurnOutputRegistry,
    reactions: ReactionLifecycleManager | None = None,
    feedback: FeishuFeedbackService | None = None,
) -> None:
    """Run the Feishu WebSocket loop until ``stop_event`` is set.

    ``Client.start()`` blocks and runs its own asyncio loop, so it must be called
    from this background thread (never wrapped in ``asyncio.run``). ``ready_event``
    is set by :class:`_ReadyOnConnectClient` only after the socket connects, and a
    stop watcher on that client unblocks ``start()`` once ``stop_event`` is set.
    Credentials were verified in the caller before this thread started, so a
    credential failure out of ``start()`` is a runtime error logged here.
    """
    session_resolver = SessionResolver(bindings, platform=_PLATFORM_FEISHU)
    active_cancels = ActiveTurnRegistry()
    conversation_locks = ConversationLockRegistry()
    turn_slots = threading.BoundedSemaphore(settings.max_concurrent_turns)
    approvals = ApprovalBroker()
    pending_approvals = PendingApprovals()

    def send_text(chat_id: str, text: str) -> str:
        return _send_text(settings.app_id, settings.app_secret, chat_id, text)

    def on_message(data: P2ImMessageReceiveV1) -> None:
        try:
            _handle_event(data)
        except Exception:
            logger.error("[feishu-gateway] inbound event handling failed", exc_info=True)

    def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        try:
            return handle_card_action(
                data,
                broker=approvals,
                pending_approvals=pending_approvals,
                env_allowed_open_ids=settings.allowed_open_ids,
                logger=logger,
                feedback=feedback,
            )
        except Exception:
            logger.error("[feishu-gateway] card callback handling failed")
            return P2CardActionTriggerResponse(
                {
                    "toast": {
                        "type": "error",
                        "content": "This interaction could not be completed",
                    }
                }
            )

    def _handle_event(data: P2ImMessageReceiveV1) -> None:
        if stop_event.is_set():
            return
        event = data.event
        if event is None:
            return
        sender = event.sender
        if sender is None or sender.sender_type != "user":
            return
        message = event.message
        if message is None:
            return
        inbound = build_inbound_message(sender, message)
        if inbound is None:
            # INFO, not DEBUG: a message type nobody handles used to be invisible
            # in a gateway process configured for INFO.
            logger.info(
                "[feishu-gateway] no turn for message type=%s chat=%s",
                message.message_type or "",
                message.chat_id or "",
            )
            return
        # No mention gate here: the app holds im:message.p2p_msg:readonly plus
        # im:message.group_at_msg[:.include_bot]:readonly and NOT
        # im:message.group_msg, so Feishu already delivers only DMs and group
        # messages that @-mention this bot. A message reaching this point is
        # therefore addressed to the bot.
        if is_stop_command(inbound.text):
            if not active_cancels.request_stop(conversation_key(inbound)):
                send_text(inbound.chat_id, NO_ACTIVE_TURN_MESSAGE)
            return

        _dispatch_turn(
            inbound,
            settings=settings,
            session_resolver=session_resolver,
            active_cancels=active_cancels,
            conversation_locks=conversation_locks,
            approvals=approvals,
            pending_approvals=pending_approvals,
            send_text=send_text,
            handler=handler,
            logger=logger,
            executor=executor,
            loop=asyncio.get_running_loop(),
            turn_slots=turn_slots,
            output_registry=output_registry,
            reactions=reactions,
            feedback=feedback,
        )

    dispatcher_handler = (
        EventDispatcherHandler.builder(encrypt_key="", verification_token="")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_card_action_trigger(on_card_action)
        .build()
    )
    client = _ReadyOnConnectClient(
        settings.app_id,
        settings.app_secret,
        # INFO logs the complete WebSocket URL, including temporary access
        # parameters. Keep SDK transport logs at WARNING while our own gateway
        # logger reports connection readiness without credentials.
        log_level=lark.LogLevel.WARNING,
        event_handler=dispatcher_handler,
        ready_event=ready_event,
        stop_event=stop_event,
    )
    try:
        client.start()
    except Exception:
        logger.critical("[feishu-gateway] fatal error in gateway thread", exc_info=True)
    finally:
        # Deny every outstanding approval so a turn parked in ``broker.wait`` is
        # released instead of holding its executor thread for the full timeout.
        pending_approvals.drain()
        approvals.close()
        if reactions is not None:
            reactions.shutdown(timeout_seconds=0)
        if feedback is not None:
            feedback.shutdown(timeout_seconds=0)


__all__ = [
    "build_inbound_message",
    "run_feishu_gateway_thread",
    "strip_leading_feishu_mentions",
]
