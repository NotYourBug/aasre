"""Feishu Gateway WebSocket worker (lark-oapi)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
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
    event is set immediately before ``start()``. A credential failure raises
    ``ObtainAccessTokenException`` out of ``start()`` and is logged here.
    """
    session_resolver = SessionResolver(bindings, platform=_PLATFORM_FEISHU)
    active_cancels = ActiveTurnRegistry()
    conversation_locks = ConversationLockRegistry()

    def send_text(chat_id: str, text: str) -> None:
        _send_text(settings.app_id, settings.app_secret, chat_id, text)

    def _consume_turn_future(future: asyncio.Future[None]) -> None:
        try:
            future.result()
        except Exception:
            logger.error("[feishu-gateway] turn dispatch failed", exc_info=True)

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
        chat_id = message.chat_id or ""
        message_id = message.message_id or ""
        text = str(json.loads(message.content or "{}").get("text", "") or "")
        sender_id = sender.sender_id
        open_id = (sender_id.open_id or "") if sender_id is not None else ""
        if not chat_id or not open_id:
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
        )
        future = asyncio.get_running_loop().run_in_executor(executor, dispatch)
        future.add_done_callback(_consume_turn_future)

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
