"""Metadata-only reply-parent verification through the pinned Feishu SDK."""

from __future__ import annotations

from dataclasses import dataclass

import lark_oapi as lark
from lark_oapi.api.im.v1 import GetMessageRequest


@dataclass(frozen=True)
class FeishuMessageMetadata:
    message_id: str
    chat_id: str


def lookup_reply_parent(
    app_id: str, app_secret: str, message_id: str, expected_chat_id: str
) -> FeishuMessageMetadata | None:
    """Return only exact live parent metadata from the approved chat."""
    if not all((app_id.strip(), app_secret.strip(), message_id.strip(), expected_chat_id.strip())):
        return None
    try:
        client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        request = GetMessageRequest.builder().message_id(message_id).build()
        response = client.im.v1.message.get(request)
        if not response.success():
            return None
        data = getattr(response, "data", None)
        items = getattr(data, "items", None)
        if not isinstance(items, list) or len(items) != 1:
            return None
        item = items[0]
        actual_message_id = str(getattr(item, "message_id", "") or "")
        actual_chat_id = str(getattr(item, "chat_id", "") or "")
        if (
            actual_message_id != message_id
            or actual_chat_id != expected_chat_id
            or getattr(item, "deleted", None) is not False
        ):
            return None
        return FeishuMessageMetadata(message_id=actual_message_id, chat_id=actual_chat_id)
    except Exception:
        return None


__all__ = ["FeishuMessageMetadata", "lookup_reply_parent"]
