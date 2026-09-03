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


__all__ = ["FeishuInboundMessage"]
