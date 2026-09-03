"""Handlers for inbound Feishu messages."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext

from config.constants.gateway import (
    CREDITS_DENIED_MESSAGE,
    TURN_ERROR_MESSAGE,
    TURN_TIMEOUT_MESSAGE,
    USER_STOP_MESSAGE,
)
from config.scope_context import bound_storage_scope
from gateway.core.billing.turn_metering import bound_turn_metering
from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.conversation_locks import ConversationLockRegistry
from gateway.core.middleware.terminal_outcome import TerminalOutcomeArbiter
from gateway.core.storage import SessionResolver
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.inbound_security import enforce_inbound_feishu_message_security
from gateway.transports.feishu.principal import PrincipalResolutionError, resolve_feishu_scope
from gateway.transports.feishu.session_rotation import conversation_key, resolve_or_rotate_session
from gateway.transports.feishu.settings import FeishuGatewaySettings
from gateway.transports.feishu.turn_output import FeishuTurnOutput
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from infrastructure.turn_host.turn_callback import TurnCallback


def _run_turn(
    inbound: FeishuInboundMessage,
    *,
    settings: FeishuGatewaySettings,
    session_resolver: SessionResolver,
    active_cancels: ActiveTurnRegistry,
    conversation_locks: ConversationLockRegistry,
    send_text: Callable[[str, str], None],
    handler: TurnCallback,
    logger: logging.Logger,
    turn_cancel: threading.Event | None = None,
) -> None:
    """Run one inbound Feishu message through the gateway agent callback.

    Runs on the turn executor thread. The owning scope is resolved first, then
    the turn is serialized per conversation, bound to the storage/usage/metering
    contexts, and driven under a cooperative timeout with ``/stop`` cancellation.

    ``turn_cancel`` is the Event the dispatcher already registered for this
    conversation, so a ``/stop`` arriving before the turn started is honoured
    rather than lost.
    """
    key = conversation_key(inbound)
    try:
        scope = resolve_feishu_scope(open_id=inbound.open_id)
    except PrincipalResolutionError:
        logger.error(
            "[feishu-gateway] turn refused: unresolved principal open_id=%s chat=%s",
            inbound.open_id,
            inbound.chat_id,
            exc_info=True,
        )
        return

    with conversation_locks.hold(key):
        decision = enforce_inbound_feishu_message_security(
            user_id=inbound.open_id,
            chat_id=inbound.chat_id,
            text=inbound.text,
            env_allowed_open_ids=settings.allowed_open_ids,
        )

        def _send(text: str) -> None:
            send_text(inbound.chat_id, text)

        with bound_storage_scope(scope):
            session = resolve_or_rotate_session(
                inbound,
                decision,
                session_resolver=session_resolver,
                scope=scope,
                send=_send,
            )
        if session is None:
            return

        preview = inbound.text.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = f"{preview[:77]}..."
        logger.info(
            "inbound open_id=%s chat=%s session=%s text=%r",
            inbound.open_id,
            inbound.chat_id,
            session.session_id[:8],
            preview,
        )

        output = FeishuTurnOutput(
            app_id=settings.app_id,
            app_secret=settings.app_secret,
            chat_id=inbound.chat_id,
            edit_interval_seconds=settings.status_update_interval_seconds,
        )
        terminal = TerminalOutcomeArbiter(turn_cancel)
        output.turn_cancel = terminal.cancel_event

        def _on_turn_timeout() -> None:
            logger.warning(
                "[feishu-gateway] turn TIMED OUT after %.0fs chat=%s session=%s",
                settings.turn_timeout_seconds,
                inbound.chat_id,
                session.session_id[:8],
            )
            try:
                output.finalize(TURN_TIMEOUT_MESSAGE)
            except Exception:
                logger.debug("[feishu-gateway] timeout finalize failed", exc_info=True)

        def _on_user_stop() -> None:
            if not terminal.claim():
                return
            try:
                output.finalize(USER_STOP_MESSAGE)
            except Exception:
                logger.debug("[feishu-gateway] user-stop finalize failed", exc_info=True)

        def _on_credit_denied() -> None:
            logger.info(
                "[feishu-gateway] turn denied: out of credits chat=%s",
                inbound.chat_id,
            )
            if terminal.claim():
                try:
                    output.finalize(CREDITS_DENIED_MESSAGE)
                except Exception:
                    logger.debug("[feishu-gateway] credits-denied finalize failed", exc_info=True)

        if turn_cancel is None:
            registration: AbstractContextManager[None] = active_cancels.track(
                key,
                terminal.cancel_event,
                on_user_stop=_on_user_stop,
            )
        else:
            active_cancels.bind_user_stop(key, turn_cancel, _on_user_stop)
            registration = nullcontext()

        # A /stop that landed between dispatch registration and here only set
        # the Event; nothing has run yet, so answer it instead of the agent.
        if terminal.cancel_event.is_set():
            if terminal.claim():
                try:
                    output.finalize(USER_STOP_MESSAGE)
                except Exception:
                    logger.debug("[feishu-gateway] user-stop finalize failed", exc_info=True)
            return

        with terminal.timeout_after(settings.turn_timeout_seconds, _on_turn_timeout):
            try:
                with (
                    registration,
                    bound_storage_scope(scope),
                    bound_usage_context(
                        surface=UsageSurface.FEISHU,
                        session_id=session.session_id,
                        user_id=inbound.open_id or None,
                    ),
                    bound_turn_metering(
                        organization_id=scope.principal.id,
                        reason="feishu_turn",
                        on_denied=_on_credit_denied,
                    ),
                ):
                    handler(inbound.text, session, output, logger)
            except Exception:
                logger.exception(
                    "[feishu-gateway] turn ERRORED chat=%s session=%s",
                    inbound.chat_id,
                    session.session_id[:8],
                )
                if terminal.claim():
                    try:
                        output.render_error(TURN_ERROR_MESSAGE)
                    except Exception:
                        logger.debug("[feishu-gateway] error finalize failed", exc_info=True)
                raise

        if terminal.claim():
            logger.info(
                "[feishu-gateway] turn done chat=%s session=%s",
                inbound.chat_id,
                session.session_id[:8],
            )


__all__ = ["_run_turn"]
