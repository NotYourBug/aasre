"""Normalize Feishu gateway events into inbound turn payloads."""

from __future__ import annotations

from dataclasses import dataclass

from integrations.feishu import ResourceRef


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
    #: Downloadable resources the sender attached, in the order they were sent —
    #: a screenshot's image, a log file, a video's cover frame. Empty for a plain
    #: text message, which is the common case.
    attachments: tuple[ResourceRef, ...] = ()


__all__ = ["FeishuInboundMessage"]
