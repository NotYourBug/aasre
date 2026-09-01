"""Inbound authorization helpers for the Feishu gateway.

Feishu v1 carries no identity allowlist, so inbound security is permissive:
a bare ``/new`` rotates the session and every other message is allowed. The
decision dataclass mirrors the other transports' shape so the shared
``apply_inbound_decision`` step can consume it, but this module never touches
the identity-policy store (no ``load_identity_policy`` / ``authorize_*`` here).
"""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.gateway import ROTATE_SESSION


@dataclass(frozen=True)
class FeishuInboundDecision:
    """Authorization outcome for one inbound Feishu message."""

    allowed: bool
    reply_text: str = ""
    persist_policy: bool = False
    updated_policy: object | None = None


def enforce_inbound_feishu_message_security(*, text: str) -> FeishuInboundDecision:
    """Authorize an inbound Feishu message: ``/new`` rotates, else allow."""
    if text.strip().lower() == "/new":
        return FeishuInboundDecision(allowed=True, reply_text=ROTATE_SESSION)
    return FeishuInboundDecision(allowed=True)


__all__ = ["FeishuInboundDecision", "enforce_inbound_feishu_message_security"]
