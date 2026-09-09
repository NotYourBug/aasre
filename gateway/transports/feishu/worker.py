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
from lark_oapi.ws.client import Client
from lark_oapi.ws.client import loop as _ws_loop

from config.constants.gateway import NO_ACTIVE_TURN_MESSAGE
from gateway.core.middleware.active_turns import ActiveTurnRegistry, is_stop_command
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.middleware.conversation_locks import ConversationLockRegistry
from gateway.core.storage import SessionResolver
from gateway.core.storage.session.binding_store import BindingStore
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.inbound_handler import _run_turn as handle_inbound_turn
from gateway.transports.feishu.inbound_security import is_open_id_authorized
from gateway.transports.feishu.pending_approvals import PendingApprovals
from gateway.transports.feishu.session_rotation import conversation_key
from gateway.transports.feishu.settings import FeishuGatewaySettings
from gateway.transports.feishu.turn_output import _send_text
from infrastructure.turn_host.turn_callback import TurnCallback

_PLATFORM_FEISHU = "feishu"

_APPROVE_WORDS = frozenset({"approve", "approved", "approves", "yes", "y", "ok", "okay", "lgtm"})
_DENY_WORDS = frozenset({"deny", "denied", "denies", "no", "n", "reject", "rejected", "cancel"})


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


def _decision(text: str) -> bool | None:
    """Read a reply as approve/deny, or ``None`` when it is neither."""
    words = text.strip().lower().split(maxsplit=1)
    if not words:
        return None
    first = words[0].strip("*_`.!,:")
    if first in _APPROVE_WORDS:
        return True
    if first in _DENY_WORDS:
        return False
    return None


def _resolve_approval_reply(
    *,
    parent_id: str,
    open_id: str,
    chat_id: str,
    text: str,
    approvals: ApprovalBroker,
    pending_approvals: PendingApprovals,
    env_allowed_open_ids: list[str],
    logger: logging.Logger,
) -> bool:
    """Resolve a reply aimed at a pending approval prompt, if any.

    Returns whether the reply was consumed here — a reply aimed at a live
    prompt never falls through to start a chat turn, decided or not.

    Runs on the WS loop's own thread — never the turn executor, so a waiting
    turn's ``ApprovalBroker.wait`` never contends with the lock it is blocked
    on. Three checks gate the decision, and none consume the prompt when they
    fail: the responder is still an authorized identity now, they are the
    member whose own turn raised the request replying in the chat it was
    posted to, and the reply actually says approve or deny.
    """
    if pending_approvals.find(parent_id) is None:
        return False

    if not is_open_id_authorized(
        open_id=open_id, chat_id=chat_id, env_allowed_open_ids=env_allowed_open_ids
    ):
        logger.warning(
            "[feishu-gateway] ignoring approval reply from unauthorized open_id=%s chat=%s",
            open_id,
            chat_id,
        )
        return True

    approved = _decision(text)
    if approved is None:
        logger.info(
            "[feishu-gateway] approval reply was not a decision open_id=%s chat=%s",
            open_id,
            chat_id,
        )
        return True

    approval_id = pending_approvals.claim(parent_id, open_id=open_id, chat_id=chat_id)
    if approval_id is None:
        logger.warning(
            "[feishu-gateway] ignoring approval reply from a member who did not "
            "raise the request open_id=%s chat=%s",
            open_id,
            chat_id,
        )
        return True

    approvals.resolve(approval_id, approved=approved, decided_by=open_id)
    return True


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
        mentions = message.mentions or []
        bot_mention_keys = frozenset(m.key for m in mentions if m.mentioned_type == "bot" and m.key)
        text = strip_leading_feishu_mentions(
            str(json.loads(message.content or "{}").get("text", "") or ""),
            bot_mention_keys,
        )
        parent_id = message.parent_id or ""
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
            parent_id=parent_id,
        )
        if parent_id and _resolve_approval_reply(
            parent_id=parent_id,
            open_id=open_id,
            chat_id=chat_id,
            text=text,
            approvals=approvals,
            pending_approvals=pending_approvals,
            env_allowed_open_ids=settings.allowed_open_ids,
            logger=logger,
        ):
            return

        # No mention gate here: the app holds im:message.p2p_msg:readonly plus
        # im:message.group_at_msg[:.include_bot]:readonly and NOT
        # im:message.group_msg, so Feishu already delivers only DMs and group
        # messages that @-mention this bot. A message reaching this point is
        # therefore addressed to the bot.
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
            approvals=approvals,
            pending_approvals=pending_approvals,
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
    client = _ReadyOnConnectClient(
        settings.app_id,
        settings.app_secret,
        log_level=lark.LogLevel.INFO,
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
        approvals.close()


__all__ = [
    "run_feishu_gateway_thread",
    "strip_leading_feishu_mentions",
]
