"""Feishu Gateway WebSocket worker (lark-oapi)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.core.token import TokenManager
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.ws import Client

from config.constants.gateway import NO_ACTIVE_TURN_MESSAGE
from gateway.core.middleware.active_turns import ActiveTurnRegistry, is_stop_command
from gateway.core.middleware.conversation_locks import ConversationLockRegistry
from gateway.core.storage import SessionResolver
from gateway.core.storage.session.binding_store import BindingStore
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.inbound_handler import handle_inbound_feishu_message
from gateway.transports.feishu.session_rotation import conversation_key
from gateway.transports.feishu.settings import FeishuGatewaySettings
from gateway.transports.feishu.turn_output import _send_text
from infrastructure.turn_host.turn_callback import TurnCallback

_PLATFORM_FEISHU = "feishu"


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
    send_text: Callable[[str, str], None],
    handler: TurnCallback,
    logger: logging.Logger,
    executor: ThreadPoolExecutor,
    loop: asyncio.AbstractEventLoop,
    turn_slots: threading.BoundedSemaphore,
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

    dispatch = partial(
        handle_inbound_feishu_message,
        inbound,
        settings=settings,
        session_resolver=session_resolver,
        active_cancels=active_cancels,
        conversation_locks=conversation_locks,
        send_text=send_text,
        handler=handler,
        logger=logger,
        turn_cancel=turn_cancel,
    )

    def _on_turn_done(future: asyncio.Future[None]) -> None:
        turn_slots.release()
        active_cancels.unregister(key, turn_cancel)
        try:
            future.result()
        except Exception:
            logger.error("[feishu-gateway] turn dispatch failed", exc_info=True)

    future = loop.run_in_executor(executor, dispatch)
    future.add_done_callback(_on_turn_done)


def run_feishu_gateway_thread(
    *,
    settings: FeishuGatewaySettings,
    logger: logging.Logger,
    handler: TurnCallback,
    bindings: BindingStore,
    executor: ThreadPoolExecutor,
    stop_event: threading.Event,
    ready_event: threading.Event,
) -> None:
    """Run the Feishu WebSocket loop until ``stop_event`` is set.

    ``Client.start()`` blocks and runs its own asyncio loop, so it must be called
    from this background thread (never wrapped in ``asyncio.run``). There is no
    first-connect callback, so "ready" means the loop is up and connecting; the
    event is set immediately before ``start()``. Credentials were verified in the
    caller before this thread started, so a credential failure out of ``start()``
    is a runtime error logged here.
    """
    session_resolver = SessionResolver(bindings, platform=_PLATFORM_FEISHU)
    active_cancels = ActiveTurnRegistry()
    conversation_locks = ConversationLockRegistry()
    turn_slots = threading.BoundedSemaphore(settings.max_concurrent_turns)

    def send_text(chat_id: str, text: str) -> None:
        _send_text(settings.app_id, settings.app_secret, chat_id, text)

    def on_message(data: P2ImMessageReceiveV1) -> None:
        try:
            _handle_event(data)
        except Exception:
            logger.error("[feishu-gateway] inbound event handling failed", exc_info=True)

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
        if message.message_type != "text":
            logger.debug(
                "[feishu-gateway] dropping non-text message type=%s chat=%s",
                message.message_type,
                message.chat_id or "",
            )
            return
        chat_id = message.chat_id or ""
        message_id = message.message_id or ""
        text = str(json.loads(message.content or "{}").get("text", "") or "")
        sender_id = sender.sender_id
        open_id = (sender_id.open_id or "") if sender_id is not None else ""
        if not chat_id or not open_id or not text:
            logger.debug(
                "[feishu-gateway] dropping incomplete message chat=%s open_id=%s text=%s",
                chat_id,
                open_id,
                bool(text),
            )
            return

        inbound = FeishuInboundMessage(
            chat_id=chat_id,
            open_id=open_id,
            message_id=message_id,
            text=text,
        )
        if is_stop_command(text):
            if not active_cancels.request_stop(conversation_key(inbound)):
                send_text(chat_id, NO_ACTIVE_TURN_MESSAGE)
            return

        _dispatch_turn(
            inbound,
            settings=settings,
            session_resolver=session_resolver,
            active_cancels=active_cancels,
            conversation_locks=conversation_locks,
            send_text=send_text,
            handler=handler,
            logger=logger,
            executor=executor,
            loop=asyncio.get_running_loop(),
            turn_slots=turn_slots,
        )

    dispatcher_handler = (
        EventDispatcherHandler.builder(encrypt_key="", verification_token="")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    client = Client(
        settings.app_id,
        settings.app_secret,
        log_level=lark.LogLevel.INFO,
        event_handler=dispatcher_handler,
    )
    ready_event.set()
    try:
        client.start()
    except Exception:
        logger.critical("[feishu-gateway] fatal error in gateway thread", exc_info=True)


__all__ = ["run_feishu_gateway_thread"]
