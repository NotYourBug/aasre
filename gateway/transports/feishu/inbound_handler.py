"""Handlers for inbound Feishu messages."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from enum import StrEnum

from config.constants.gateway import (
    CREDITS_DENIED_MESSAGE,
    ROTATE_SESSION,
    TURN_ERROR_MESSAGE,
    TURN_TIMEOUT_MESSAGE,
    USER_STOP_MESSAGE,
)
from config.principal import StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness import SessionCore
from gateway.core.billing.turn_metering import bound_turn_metering
from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker, approval_tool_hooks
from gateway.core.middleware.conversation_locks import ConversationLockRegistry
from gateway.core.middleware.identity_policy import persist_policy_if_needed
from gateway.core.middleware.terminal_outcome import TerminalOutcomeArbiter
from gateway.core.storage import SessionResolver
from gateway.transports.feishu.approvals import FeishuApprovalPrompter
from gateway.transports.feishu.attachments import (
    Downloader,
    build_attachments_context,
    feishu_resource_downloader,
)
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.feedback import FeishuFeedbackService
from gateway.transports.feishu.final_actions import FinalActionCoordinator
from gateway.transports.feishu.inbound_security import enforce_inbound_feishu_message_security
from gateway.transports.feishu.pending_approvals import PendingApprovals
from gateway.transports.feishu.principal import PrincipalResolutionError, resolve_feishu_scope
from gateway.transports.feishu.reply_actions import (
    ReplyActionHandle,
    ReplyActionRegistry,
)
from gateway.transports.feishu.session_rotation import conversation_key, resolve_or_rotate_session
from gateway.transports.feishu.settings import FeishuGatewaySettings
from gateway.transports.feishu.turn_output import FeishuTurnOutput, FeishuTurnOutputRegistry
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from infrastructure.turn_host.turn_callback import TurnCallback
from integrations.feishu.card_client import FeishuCardClient

#: Stands in when the attachment layer fails outright. A caption-less screenshot
#: has no text of its own, so returning its empty text would hand the agent
#: nothing at all — the exact outcome this slice exists to remove.
_UNREADABLE_ATTACHMENT = "- attachment — could not be read"


@dataclass(frozen=True, slots=True)
class PreparedFeishuTurn:
    """An authorized Feishu turn whose attachment context is already normalized."""

    inbound: FeishuInboundMessage
    prompt: str
    session_id: str


class TurnExecutionResult(StrEnum):
    """Outcome of attempting to enter the shared Feishu turn lifecycle."""

    DISPATCHED = "dispatched"
    CANCELLED = "cancelled"
    SESSION_MISMATCH = "session_mismatch"
    PRINCIPAL_UNAVAILABLE = "principal_unavailable"


def _with_attachment_context(
    inbound: FeishuInboundMessage,
    settings: FeishuGatewaySettings,
    downloader: Downloader | None,
    logger: logging.Logger,
) -> str:
    """The turn's prompt text: the message, plus what its attachments say.

    A failure here degrades to the message alone rather than failing the turn —
    an attachment the agent cannot read must never surface as an error reply.
    """
    build = downloader or feishu_resource_downloader(settings.app_id, settings.app_secret)
    try:
        section = build_attachments_context(
            inbound.message_id, inbound.attachments, downloader=build
        )
    except Exception as exc:
        logger.warning("[feishu-gateway] attachment context failed type=%s", type(exc).__name__)
        section = ""
    if not section:
        return inbound.text or _UNREADABLE_ATTACHMENT
    return f"{inbound.text}\n\n{section}" if inbound.text else section


def _execute_prepared_turn(
    prepared: PreparedFeishuTurn,
    *,
    session: SessionCore,
    scope: StorageScope,
    settings: FeishuGatewaySettings,
    active_cancels: ActiveTurnRegistry,
    approvals: ApprovalBroker,
    pending_approvals: PendingApprovals,
    handler: TurnCallback,
    logger: logging.Logger,
    turn_cancel: threading.Event | None = None,
    output_registry: FeishuTurnOutputRegistry | None = None,
    feedback: FeishuFeedbackService | None = None,
    reply_actions: ReplyActionRegistry | None = None,
    final_actions: FinalActionCoordinator | None = None,
    action_handle: ReplyActionHandle | None = None,
) -> TurnExecutionResult:
    """Execute one normalized prompt while its conversation lock is held."""
    inbound = prepared.inbound
    key = conversation_key(inbound)
    terminal = TerminalOutcomeArbiter(turn_cancel)
    message_observer: Callable[[str], None] | None = None
    action_invalidator: Callable[[], None] | None = None
    if reply_actions is not None and action_handle is not None:

        def message_observer(message_id: str) -> None:
            reply_actions.observe_message(action_handle.generation_id, message_id)

        def action_invalidator() -> None:
            reply_actions.invalidate(action_handle.generation_id)

    output = FeishuTurnOutput(
        app_id=settings.app_id,
        app_secret=settings.app_secret,
        chat_id=inbound.chat_id,
        reply_to_message_id=inbound.root_id or inbound.message_id,
        reply_in_thread=bool(inbound.root_id),
        edit_interval_seconds=settings.status_update_interval_seconds,
        tool_hooks=approval_tool_hooks(
            FeishuApprovalPrompter(
                broker=approvals,
                card_client=FeishuCardClient(settings.app_id, settings.app_secret),
                chat_id=inbound.chat_id,
                requester_open_id=inbound.open_id,
                pending_approvals=pending_approvals,
            )
        ),
        turn_cancel=terminal.cancel_event,
        output_registry=output_registry,
        message_observer=message_observer,
        on_action_invalidated=action_invalidator,
    )

    def _on_turn_timeout() -> None:
        output.disqualify_feedback()
        logger.warning(
            "[feishu-gateway] turn timed out after %.0fs",
            settings.turn_timeout_seconds,
        )
        try:
            output.finalize(TURN_TIMEOUT_MESSAGE)
        except Exception:
            logger.debug("[feishu-gateway] timeout finalize failed")

    def _on_user_stop() -> None:
        if not terminal.claim():
            return
        output.disqualify_feedback()
        try:
            output.finalize(USER_STOP_MESSAGE)
        except Exception:
            logger.debug("[feishu-gateway] user-stop finalize failed")

    def _on_credit_denied() -> None:
        logger.info("[feishu-gateway] turn denied: out of credits")
        if terminal.claim():
            try:
                output.disqualify_feedback()
                output.finalize(CREDITS_DENIED_MESSAGE)
            except Exception:
                logger.debug("[feishu-gateway] credits-denied finalize failed")

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
                logger.debug("[feishu-gateway] user-stop finalize failed")
        return TurnExecutionResult.CANCELLED

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
                handler(prepared.prompt, session, output, logger)
        except Exception as exc:
            logger.error("[feishu-gateway] turn errored type=%s", type(exc).__name__)
            if terminal.claim():
                try:
                    output.disqualify_feedback()
                    output.render_error(TURN_ERROR_MESSAGE)
                except Exception:
                    logger.debug("[feishu-gateway] error finalize failed")
            raise

    if terminal.claim():
        target = output.take_feedback_target()
        if feedback is not None and target is not None:
            if final_actions is not None and action_handle is not None:
                final_actions.issue(
                    action_handle,
                    target,
                    requester_open_id=inbound.open_id,
                    chat_id=inbound.chat_id,
                )
            else:
                issue = getattr(feedback, "issue", None)
                if issue is not None:
                    issue(target, requester_open_id=inbound.open_id, chat_id=inbound.chat_id)
        logger.info("[feishu-gateway] turn done")
    return TurnExecutionResult.DISPATCHED


def run_prepared_turn(
    prepared: PreparedFeishuTurn,
    *,
    expected_session_id: str | None,
    settings: FeishuGatewaySettings,
    session_resolver: SessionResolver,
    active_cancels: ActiveTurnRegistry,
    conversation_locks: ConversationLockRegistry,
    approvals: ApprovalBroker,
    pending_approvals: PendingApprovals,
    handler: TurnCallback,
    logger: logging.Logger,
    turn_cancel: threading.Event | None = None,
    output_registry: FeishuTurnOutputRegistry | None = None,
    feedback: FeishuFeedbackService | None = None,
    reply_actions: ReplyActionRegistry | None = None,
    final_actions: FinalActionCoordinator | None = None,
) -> TurnExecutionResult:
    """Run an authorized normalized prompt only in its still-current session."""
    inbound = prepared.inbound
    key = conversation_key(inbound)
    with conversation_locks.hold(key):
        try:
            scope = resolve_feishu_scope(open_id=inbound.open_id)
        except PrincipalResolutionError:
            logger.error("[feishu-gateway] prepared turn refused: unresolved principal")
            return TurnExecutionResult.PRINCIPAL_UNAVAILABLE

        with bound_storage_scope(scope):
            session = session_resolver.resolve(
                user_id=key,
                chat_id=inbound.chat_id,
                principal=scope.principal,
                actor=scope.actor,
            )
        if expected_session_id is not None and session.session_id != expected_session_id:
            logger.info("[feishu-gateway] prepared turn refused: session changed")
            return TurnExecutionResult.SESSION_MISMATCH

        action_handle = (
            reply_actions.begin(inbound, prompt=prepared.prompt, session_id=session.session_id)
            if reply_actions is not None
            else None
        )
        return _execute_prepared_turn(
            prepared,
            session=session,
            scope=scope,
            settings=settings,
            active_cancels=active_cancels,
            approvals=approvals,
            pending_approvals=pending_approvals,
            handler=handler,
            logger=logger,
            turn_cancel=turn_cancel,
            output_registry=output_registry,
            feedback=feedback,
            reply_actions=reply_actions,
            final_actions=final_actions,
            action_handle=action_handle,
        )


def _run_turn(
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
    turn_cancel: threading.Event | None = None,
    downloader: Downloader | None = None,
    output_registry: FeishuTurnOutputRegistry | None = None,
    feedback: FeishuFeedbackService | None = None,
    reply_actions: ReplyActionRegistry | None = None,
    final_actions: FinalActionCoordinator | None = None,
) -> None:
    """Run one inbound Feishu message through the gateway agent callback.

    Runs on the turn executor thread. Security-only replies are handled before
    principal resolution; allowed turns are then bound to the owning storage,
    usage, and metering contexts and driven under a cooperative timeout with
    ``/stop`` cancellation.

    ``turn_cancel`` is the Event the dispatcher already registered for this
    conversation, so a ``/stop`` arriving before the turn started is honoured
    rather than lost.
    """
    key = conversation_key(inbound)
    with conversation_locks.hold(key):
        decision = enforce_inbound_feishu_message_security(
            user_id=inbound.open_id,
            chat_id=inbound.chat_id,
            text=inbound.text,
            env_allowed_open_ids=settings.allowed_open_ids,
        )

        def _send(text: str) -> None:
            send_text(inbound.chat_id, text)

        # Pairing, help and authorization denials do not own or access a turn
        # session. Apply them before resolving the deployment principal so a
        # fresh installation can bootstrap its first allowed identity even
        # when ORGANIZATION_ID has not been configured yet.
        if not decision.allowed:
            persist_policy_if_needed("feishu", decision)
            if decision.reply_text:
                _send(decision.reply_text)
            return

        try:
            scope = resolve_feishu_scope(open_id=inbound.open_id)
        except PrincipalResolutionError:
            logger.error("[feishu-gateway] turn refused: unresolved principal")
            return

        if reply_actions is not None and decision.reply_text == ROTATE_SESSION:
            reply_actions.invalidate_conversation(key)

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

        logger.info("[feishu-gateway] inbound turn accepted")

        agent_text = inbound.text
        if inbound.attachments:
            agent_text = _with_attachment_context(inbound, settings, downloader, logger)
        prepared = PreparedFeishuTurn(
            inbound=inbound,
            prompt=agent_text,
            session_id=session.session_id,
        )
        action_handle = (
            reply_actions.begin(inbound, prompt=agent_text, session_id=session.session_id)
            if reply_actions is not None
            else None
        )
        _execute_prepared_turn(
            prepared,
            session=session,
            scope=scope,
            settings=settings,
            active_cancels=active_cancels,
            approvals=approvals,
            pending_approvals=pending_approvals,
            handler=handler,
            logger=logger,
            turn_cancel=turn_cancel,
            output_registry=output_registry,
            feedback=feedback,
            reply_actions=reply_actions,
            final_actions=final_actions,
            action_handle=action_handle,
        )


__all__ = ["PreparedFeishuTurn", "TurnExecutionResult", "_run_turn", "run_prepared_turn"]
