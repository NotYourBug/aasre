"""Inbound authorization helpers for the Feishu gateway.

Feishu inbound is gated on an identity allowlist keyed on the sender's stable
``open_id`` (the analogue of Telegram's numeric ``user_id``). The decision
dataclass mirrors the other transports' shape so the shared
``apply_inbound_decision`` step can consume it.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.gateway import ROTATE_SESSION
from gateway.core.middleware.identity_policy import load_identity_policy
from integrations.messaging_security import (
    AuthorizationResult,
    MessagingIdentityPolicy,
    MessagingPlatform,
    audit_log_inbound_message,
    authorize_inbound_message,
    complete_pairing,
    message_hash,
)


@dataclass(frozen=True)
class FeishuInboundDecision:
    """Authorization outcome for one inbound Feishu message."""

    allowed: bool
    reply_text: str = ""
    persist_policy: bool = False
    updated_policy: MessagingIdentityPolicy | None = None


def enforce_inbound_feishu_message_security(
    *,
    user_id: str,
    chat_id: str,
    text: str,
    env_allowed_open_ids: list[str],
) -> FeishuInboundDecision:
    """Authorize an inbound Feishu message and handle ``/pair`` attempts.

    ``user_id`` carries the sender's ``open_id`` — Feishu's stable per-user id.
    """
    _record, policy = load_identity_policy(MessagingPlatform.FEISHU.value)
    if env_allowed_open_ids and not policy.allowed_user_ids:
        policy.allowed_user_ids = list(env_allowed_open_ids)
        policy.inbound_enabled = True

    if text.strip().lower().startswith("/pair "):
        code = text.strip().split(maxsplit=1)[1] if " " in text.strip() else ""
        ok, msg = complete_pairing(policy=policy, user_id=user_id, code=code)
        audit_log_inbound_message(
            platform=MessagingPlatform.FEISHU.value,
            user_id=user_id,
            chat_id=chat_id,
            message_hash=message_hash(text),
            authorized=ok,
            reason=msg,
        )
        return FeishuInboundDecision(
            allowed=False,
            reply_text=msg,
            persist_policy=True,
            updated_policy=policy,
        )

    if text.strip().lower() in {"/start", "/help"}:
        audit_log_inbound_message(
            platform=MessagingPlatform.FEISHU.value,
            user_id=user_id,
            chat_id=chat_id,
            message_hash=message_hash(text),
            authorized=True,
            reason="builtin command",
        )
        return FeishuInboundDecision(
            allowed=False,
            reply_text=(
                "OpenSRE Feishu gateway.\n"
                "Send a message to chat with the agent.\n"
                "Commands: /new (new session), /help"
            ),
        )

    result: AuthorizationResult = authorize_inbound_message(
        policy=policy,
        user_id=user_id,
        chat_id=chat_id,
        message_text=text,
    )

    if text.strip().lower() == "/new":
        if not result:
            audit_log_inbound_message(
                platform=MessagingPlatform.FEISHU.value,
                user_id=user_id,
                chat_id=chat_id,
                message_hash=message_hash(text),
                authorized=False,
                reason=result.reason,
            )
            return FeishuInboundDecision(allowed=False, reply_text=result.reason)
        audit_log_inbound_message(
            platform=MessagingPlatform.FEISHU.value,
            user_id=user_id,
            chat_id=chat_id,
            message_hash=message_hash(text),
            authorized=True,
            reason="session rotate",
        )
        return FeishuInboundDecision(allowed=True, reply_text=ROTATE_SESSION)

    audit_log_inbound_message(
        platform=MessagingPlatform.FEISHU.value,
        user_id=user_id,
        chat_id=chat_id,
        message_hash=message_hash(text),
        authorized=bool(result),
        reason=result.reason,
    )
    if result:
        return FeishuInboundDecision(allowed=True)
    return FeishuInboundDecision(allowed=False, reply_text=result.reason)


__all__ = ["FeishuInboundDecision", "enforce_inbound_feishu_message_security"]
