"""Feishu turn output: send the final answer as one text message."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from infrastructure.text.markdown import tighten_markdown_emphasis

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
    except Exception as exc:
        logger.warning("Feishu turn output send failed: %s", exc)


class FeishuTurnOutput:
    """Deliver turn output to a Feishu chat."""

    def __init__(self, *, app_id: str, app_secret: str, chat_id: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._chat_id = chat_id

    def print(self, message: str = "") -> None:
        _ = message

    def render_response_header(self, label: str) -> None:
        _ = label

    def render_error(self, message: str) -> None:
        self.finalize(message)

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with, defer_want_me_to_closer)
        return "".join(str(c) for c in chunks)

    def set_tool_status(self, status: str) -> None:
        _ = status

    def finish_streamed_response(self, answer: str) -> None:
        self.finalize(answer)

    def finalize(self, answer: str) -> None:
        text = tighten_markdown_emphasis((answer or "").strip())
        if text:
            _send_text(self._app_id, self._app_secret, self._chat_id, text)
