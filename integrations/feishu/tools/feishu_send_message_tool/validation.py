"""Pre-approval validation for a Feishu send call."""

from __future__ import annotations

from typing import Any

from core.tool import availability_view
from integrations.feishu.outbound_targets import has_outbound_target, resolve_target_from_view


def prepare_send_input(
    payload: dict[str, Any], resolved_integrations: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Show the exact authorized target on the approval card."""
    target = payload.get("target")
    message = payload.get("message")
    if not isinstance(target, str) or not target.strip():
        return payload, "Feishu target is required"
    if not isinstance(message, str) or not message.strip():
        return payload, "Feishu message is required"
    view = availability_view(resolved_integrations)
    if not has_outbound_target(view):
        return payload, "Feishu send is unavailable"
    try:
        resolved = resolve_target_from_view(target, view)
    except (ValueError, PermissionError):
        return payload, "Feishu target is invalid or unauthorized"
    return {"target": resolved.canonical, "message": message}, None


__all__ = ["prepare_send_input"]
