"""Session resolution for inbound Feishu messages."""

from __future__ import annotations

from collections.abc import Callable

from config.principal import StorageScope
from core.agent_harness import SessionCore
from gateway.core.middleware.inbound_decision import apply_inbound_decision
from gateway.core.storage import SessionResolver
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.inbound_security import FeishuInboundDecision
from integrations.messaging_security import MessagingPlatform


def conversation_key(event: FeishuInboundMessage) -> str:
    """Session-binding key: one session per ``(chat, sender)`` pair.

    A Feishu group chat has many members, so the sender's open_id alone cannot
    isolate sessions — two different people in the same chat must not share one.
    """
    return f"{event.chat_id}:{event.open_id}"


def resolve_or_rotate_session(
    event: FeishuInboundMessage,
    decision: FeishuInboundDecision,
    *,
    session_resolver: SessionResolver,
    scope: StorageScope,
    send: Callable[[str], None],
) -> SessionCore | None:
    """Apply inbound decision side effects, then resolve or rotate the session."""
    return apply_inbound_decision(
        decision,
        platform=MessagingPlatform.FEISHU.value,
        resolver=session_resolver,
        scope=scope,
        conversation_key=conversation_key(event),
        chat_id=event.chat_id,
        text=event.text,
        send=send,
    )


__all__ = ["conversation_key", "resolve_or_rotate_session"]
