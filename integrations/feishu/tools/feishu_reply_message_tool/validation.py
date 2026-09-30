"""Pre-approval validation for a Feishu reply call."""

from __future__ import annotations

from typing import Any

from core.tool import availability_view
from integrations.feishu.outbound_targets import has_outbound_target, resolve_target_from_view


def prepare_reply_input(
    payload: dict[str, Any], resolved_integrations: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Normalize a reply to one authorized chat before requesting approval."""
    target = payload.get("target")
    message_id = payload.get("message_id")
    message = payload.get("message")
    reply_in_thread = payload.get("reply_in_thread")
    if not isinstance(target, str) or not target.strip():
        return payload, "Feishu reply target is required"
    if not isinstance(message_id, str) or not message_id.strip():
        return payload, "Feishu parent message ID is required"
    if not isinstance(message, str) or not message.strip():
        return payload, "Feishu reply body is required"
    if not isinstance(reply_in_thread, bool):
        return payload, "Feishu reply_in_thread must be boolean"
    view = availability_view(resolved_integrations)
    if not has_outbound_target(view, require_chat_id=True):
        return payload, "Feishu reply is unavailable"
    try:
        resolved = resolve_target_from_view(target, view, require_chat_id=True)
    except (ValueError, PermissionError):
        return payload, "Feishu reply target is invalid or unauthorized"
    return {
        "target": resolved.canonical,
        "message_id": message_id.strip(),
        "message": message,
        "reply_in_thread": reply_in_thread,
    }, None


__all__ = ["prepare_reply_input"]
