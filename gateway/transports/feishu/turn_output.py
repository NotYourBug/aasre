"""Feishu turn output: send the final answer as one text message."""

from __future__ import annotations

import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from gateway.core.single_message_output import SingleMessageTurnOutput
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.text.markdown import tighten_markdown_emphasis
from infrastructure.text.truncation import truncate

logger = logging.getLogger(__name__)


def _send_text(app_id: str, app_secret: str, chat_id: str, text: str) -> None:
    """Send one text message via the pinned lark-oapi SDK (Task 0)."""
    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        .build()
    )
    try:
        client.im.v1.message.create(request)
    except Exception:
        logger.exception("Feishu turn output send failed")
        raise


class _FeishuChannel:
    """Vendor I/O for a Feishu chat message delivered as one final text."""

    reopen_placeholder_on_status = False

    def __init__(self, *, app_id: str, app_secret: str, chat_id: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._chat_id = chat_id
        self.destination = f"chat={chat_id}"

    def on_status(self) -> None:
        # Feishu v1 posts no typing indicator.
        return

    def open_placeholder(self, _text: str) -> str:
        # No placeholder in v1: the final answer is the first message sent.
        return ""

    def edit_preview(self, _message_id: str, _text: str) -> bool:
        # No in-place edit in v1.
        return False

    def deliver_final(self, _message_id: str, answer: str) -> str:
        text = truncate(
            tighten_markdown_emphasis((answer or "").strip()),
            MAX_MESSAGE_SIZE,
            suffix="…",
        )
        if text:
            _send_text(self._app_id, self._app_secret, self._chat_id, text)
        return ""


class FeishuTurnOutput(SingleMessageTurnOutput):
    """Deliver turn output to a Feishu chat as one text message."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        chat_id: str,
        edit_interval_seconds: float = 1.5,
        tool_hooks: object | None = None,
    ) -> None:
        super().__init__(
            channel=_FeishuChannel(app_id=app_id, app_secret=app_secret, chat_id=chat_id),
            edit_interval_seconds=edit_interval_seconds,
            tool_hooks=tool_hooks,
        )


__all__ = ["FeishuTurnOutput"]
