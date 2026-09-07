"""Normalize Feishu gateway events into inbound turn payloads."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeishuInboundMessage:
    """Normalized inbound Feishu ``im.message.receive_v1`` event."""

    chat_id: str
    open_id: str
    message_id: str
    text: str
    #: ``message_id`` of the message this one replies to, or "" when it is not
    #: a reply. This is what matches an approval prompt to its approve/deny
    #: answer; see :mod:`gateway.transports.feishu.pending_approvals`.
    parent_id: str = ""


__all__ = ["FeishuInboundMessage"]
