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
from lark_oapi.api.im.v1 import (
    P2ImMessageReactionCreatedV1,
    P2ImMessageReactionDeletedV1,
    P2ImMessageReceiveV1,
)
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
from config.scope_context import bound_storage_scope
from gateway.core.middleware.active_turns import ActiveTurnRegistry, is_stop_command
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.middleware.conversation_locks import ConversationLockRegistry
from gateway.core.storage import SessionResolver
from gateway.core.storage.session.binding_store import BindingStore
from gateway.transports.feishu.card_actions import handle_card_action
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.feedback import FeishuFeedbackService
from gateway.transports.feishu.final_actions import FinalActionCoordinator
from gateway.transports.feishu.inbound_handler import (
    PreparedFeishuTurn,
    TurnExecutionResult,
    run_prepared_turn,
)
from gateway.transports.feishu.inbound_handler import (
    _run_turn as handle_inbound_turn,
)
from gateway.transports.feishu.inbound_security import is_feedback_actor_authorized
from gateway.transports.feishu.pending_approvals import PendingApprovals
from gateway.transports.feishu.principal import PrincipalResolutionError, resolve_feishu_scope
from gateway.transports.feishu.reaction_events import (
    FeishuReactionEvent,
    FeishuReactionService,
    normalize_reaction_created,
    normalize_reaction_deleted,
)
from gateway.transports.feishu.reply_actions import PreparedRetry, ReplyActionRegistry
from gateway.transports.feishu.retry import FeishuRetryService
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
    feedback: FeishuFeedbackService | None = None,
    reply_actions: ReplyActionRegistry | None = None,
    final_actions: FinalActionCoordinator | None = None,
    on_admitted: Callable[[str], None] | None = None,
    on_released: Callable[[str], None] | None = None,
) -> bool:
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
        logger.warning("[feishu-gateway] turn dropped: concurrency limit reached")
        active_cancels.unregister(key, turn_cancel)
        return False
    if on_admitted is not None:
        on_admitted(key)

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
        feedback=feedback,
        reply_actions=reply_actions,
        final_actions=final_actions,
    )

    def _on_turn_done(future: asyncio.Future[None]) -> None:
        turn_slots.release()
        active_cancels.unregister(key, turn_cancel)
        if on_released is not None:
            on_released(key)
        try:
            future.result()
        except Exception as exc:
            logger.error("[feishu-gateway] turn dispatch failed type=%s", type(exc).__name__)

    try:
        future = loop.run_in_executor(executor, _run_turn)
    except Exception as exc:
        # A synchronous dispatch failure (the WS loop closed during shutdown)
        # never reaches ``_on_turn_done``, so release the slot and drop the
        # cancel Event here instead of leaking both on a daemon thread.
        turn_slots.release()
        active_cancels.unregister(key, turn_cancel)
        if on_released is not None:
            on_released(key)
        logger.error(
            "[feishu-gateway] turn dispatch rejected: executor unavailable type=%s",
            type(exc).__name__,
        )
        return False
    future.add_done_callback(_on_turn_done)
    return True


def _dispatch_prepared_retry(
    prepared: PreparedRetry,
    *,
    settings: FeishuGatewaySettings,
    session_resolver: SessionResolver,
    active_cancels: ActiveTurnRegistry,
    conversation_locks: ConversationLockRegistry,
    approvals: ApprovalBroker,
    pending_approvals: PendingApprovals,
    handler: TurnCallback,
    logger: logging.Logger,
    executor: ThreadPoolExecutor,
    loop: asyncio.AbstractEventLoop,
    turn_slots: threading.BoundedSemaphore,
    output_registry: FeishuTurnOutputRegistry,
    feedback: FeishuFeedbackService,
    reply_actions: ReplyActionRegistry,
    final_actions: FinalActionCoordinator,
    on_admitted: Callable[[str], None],
    on_released: Callable[[str], None],
) -> bool:
    """Admit one claimed retry through the normal locked and metered lifecycle."""
    key = prepared.conversation_key
    turn_cancel = threading.Event()
    active_cancels.register(key, turn_cancel)
    if not turn_slots.acquire(blocking=False):
        active_cancels.unregister(key, turn_cancel)
        return False
    on_admitted(key)
    work = partial(
        run_prepared_turn,
        PreparedFeishuTurn(
            inbound=prepared.inbound,
            prompt=prepared.prompt,
            session_id=prepared.session_id,
        ),
        expected_session_id=prepared.session_id,
        settings=settings,
        session_resolver=session_resolver,
        active_cancels=active_cancels,
        conversation_locks=conversation_locks,
        approvals=approvals,
        pending_approvals=pending_approvals,
        handler=handler,
        logger=logger,
        turn_cancel=turn_cancel,
        output_registry=output_registry,
        feedback=feedback,
        reply_actions=reply_actions,
        final_actions=final_actions,
    )

    def done(future: asyncio.Future[TurnExecutionResult]) -> None:
        turn_slots.release()
        active_cancels.unregister(key, turn_cancel)
        on_released(key)
        try:
            future.result()
        except Exception as exc:
            logger.error("[feishu-gateway] retry turn dispatch failed type=%s", type(exc).__name__)

    try:
        future = loop.run_in_executor(executor, work)
    except Exception:
        turn_slots.release()
        active_cancels.unregister(key, turn_cancel)
        on_released(key)
        return False
    future.add_done_callback(done)
    return True


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
    feedback: FeishuFeedbackService | None = None,
    retry: FeishuRetryService | None = None,
    reaction_handler: Callable[[FeishuReactionEvent], None] | None = None,
    reply_actions: ReplyActionRegistry | None = None,
    final_actions: FinalActionCoordinator | None = None,
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
    dispatch_lock = threading.Lock()
    active_conversations: set[str] = set()
    deferred_retries: dict[str, PreparedRetry] = {}
    retry_service = retry
    reaction_service: FeishuReactionService | None = None

    def send_text(chat_id: str, text: str) -> str:
        return _send_text(settings.app_id, settings.app_secret, chat_id, text)

    def authorized(actor: str, chat: str) -> bool:
        return is_feedback_actor_authorized(
            open_id=actor,
            chat_id=chat,
            env_allowed_open_ids=settings.allowed_open_ids,
        )

    def current_session_id(actor: str, chat: str) -> str | None:
        try:
            scope = resolve_feishu_scope(open_id=actor)
        except PrincipalResolutionError:
            return None
        with bound_storage_scope(scope):
            return bindings.get_session_id(
                platform=_PLATFORM_FEISHU,
                chat_id=f"{chat}:{actor}",
                principal=scope.principal,
                actor=scope.actor,
            )

    def mark_admitted(key: str) -> None:
        with dispatch_lock:
            active_conversations.add(key)

    def drain_retry(key: str) -> None:
        if reply_actions is None or final_actions is None or feedback is None:
            return
        with dispatch_lock:
            if key in active_conversations:
                return
            prepared = deferred_retries.pop(key, None)
        if prepared is None:
            return
        accepted = _dispatch_prepared_retry(
            prepared,
            settings=settings,
            session_resolver=session_resolver,
            active_cancels=active_cancels,
            conversation_locks=conversation_locks,
            approvals=approvals,
            pending_approvals=pending_approvals,
            handler=handler,
            logger=logger,
            executor=executor,
            loop=_ws_loop,
            turn_slots=turn_slots,
            output_registry=output_registry,
            feedback=feedback,
            reply_actions=reply_actions,
            final_actions=final_actions,
            on_admitted=mark_admitted,
            on_released=mark_released,
        )
        if not accepted:
            reply_actions.mark_retry_unavailable(prepared.generation_id)

    def mark_released(key: str) -> None:
        with dispatch_lock:
            active_conversations.discard(key)
        if reply_actions is None:
            return
        if reaction_service is not None:
            reaction_service.handle_pending(key)
        _ws_loop.call_soon_threadsafe(drain_retry, key)

    def queue_retry(prepared: PreparedRetry) -> bool:
        if stop_event.is_set():
            return False
        with dispatch_lock:
            if prepared.conversation_key in deferred_retries:
                return False
            deferred_retries[prepared.conversation_key] = prepared
        try:
            _ws_loop.call_soon_threadsafe(drain_retry, prepared.conversation_key)
        except RuntimeError:
            with dispatch_lock:
                deferred_retries.pop(prepared.conversation_key, None)
            return False
        return True

    if reply_actions is not None and retry_service is None:
        retry_service = FeishuRetryService(
            reply_actions=reply_actions,
            authorized=authorized,
            current_session_id=current_session_id,
            dispatch=queue_retry,
        )
    if reply_actions is not None and feedback is not None:
        reaction_service = FeishuReactionService(
            reply_actions=reply_actions,
            feedback=feedback,
            authorized=authorized,
            current_session_id=current_session_id,
            dispatch_retry=queue_retry,
        )

    def on_message(data: P2ImMessageReceiveV1) -> None:
        try:
            _handle_event(data)
        except Exception as exc:
            logger.error(
                "[feishu-gateway] inbound event handling failed type=%s", type(exc).__name__
            )

    def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        try:
            return handle_card_action(
                data,
                broker=approvals,
                pending_approvals=pending_approvals,
                env_allowed_open_ids=settings.allowed_open_ids,
                logger=logger,
                feedback=feedback,
                retry=retry_service,
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

    def on_reaction_created(data: P2ImMessageReactionCreatedV1) -> None:
        try:
            normalized = normalize_reaction_created(data)
            target = reaction_handler or (reaction_service.handle if reaction_service else None)
            if normalized is not None and target is not None:
                target(normalized)
        except Exception:
            logger.error("[feishu-gateway] reaction-created handling failed")

    def on_reaction_deleted(data: P2ImMessageReactionDeletedV1) -> None:
        try:
            normalized = normalize_reaction_deleted(data)
            target = reaction_handler or (reaction_service.handle if reaction_service else None)
            if normalized is not None and target is not None:
                target(normalized)
        except Exception:
            logger.error("[feishu-gateway] reaction-deleted handling failed")

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
            logger.info("[feishu-gateway] no turn for message type=%s", message.message_type or "")
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
            feedback=feedback,
            reply_actions=reply_actions,
            final_actions=final_actions,
            on_admitted=mark_admitted,
            on_released=mark_released,
        )

    dispatcher_handler = (
        EventDispatcherHandler.builder(encrypt_key="", verification_token="")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_card_action_trigger(on_card_action)
        .register_p2_im_message_reaction_created_v1(on_reaction_created)
        .register_p2_im_message_reaction_deleted_v1(on_reaction_deleted)
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
    except Exception as exc:
        logger.critical(
            "[feishu-gateway] fatal error in gateway thread type=%s", type(exc).__name__
        )
    finally:
        # Deny every outstanding approval so a turn parked in ``broker.wait`` is
        # released instead of holding its executor thread for the full timeout.
        pending_approvals.drain()
        approvals.close()
        if retry_service is not None:
            retry_service.shutdown(timeout_seconds=0)
        if reaction_service is not None:
            reaction_service.shutdown(timeout_seconds=0)
        if feedback is not None:
            feedback.shutdown(timeout_seconds=0)


__all__ = [
    "build_inbound_message",
    "run_feishu_gateway_thread",
    "strip_leading_feishu_mentions",
]
