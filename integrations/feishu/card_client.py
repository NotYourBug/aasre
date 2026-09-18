"""Synchronous CardKit client for Feishu.

Lark is imported at module scope, mirroring ``integrations/feishu/delivery.py``.
That is why this module is **not** re-exported from the package facade — doing so
would pull the SDK onto the launch path. Consumers deep-import it and are listed
in their tier's border allowlist.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

import lark_oapi as lark
from lark_oapi.api.cardkit.v1 import (
    ContentCardElementRequest,
    ContentCardElementRequestBody,
    CreateCardRequest,
    CreateCardRequestBody,
    SettingsCardRequest,
    SettingsCardRequestBody,
)
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from config.constants import FEISHU_STREAM_ERROR_CODES

logger = logging.getLogger(__name__)


class FeishuStreamRejected(RuntimeError):
    """Raised when a cardkit call fails with a streaming-specific error code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"Feishu cardkit call failed ({code}): {message}")
        self.code = code


class FeishuCardClient:
    """Synchronous CardKit client bound to one app's credentials.

    One instance serves a whole streaming session, so the underlying lark client
    is built once rather than per call.
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        reply_to_message_id: str = "",
        reply_in_thread: bool = False,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._reply_to_message_id = reply_to_message_id
        self._reply_in_thread = reply_in_thread
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = (
                lark.Client.builder().app_id(self._app_id).app_secret(self._app_secret).build()
            )
        return self._client

    def _check(self, response: Any, *, streaming: bool) -> None:
        """Raise on a rejected call, preserving the business code."""
        if getattr(response, "success", None) is not None and response.success():
            return
        code = int(getattr(response, "code", 0) or 0)
        message = str(getattr(response, "msg", "") or "")
        logger.warning("Feishu cardkit call rejected code=%s", code)
        if streaming and code in FEISHU_STREAM_ERROR_CODES:
            raise FeishuStreamRejected(code, message)
        raise RuntimeError(f"Feishu cardkit call failed ({code}): {message}")

    def create_card(self, spec: dict[str, object]) -> str:
        """Create a card entity and return its ``card_id``."""
        request = (
            CreateCardRequest.builder()
            .request_body(
                CreateCardRequestBody.builder()
                .type("card_json")
                .data(json.dumps(spec, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self._ensure_client().cardkit.v1.card.create(request)
        self._check(response, streaming=False)
        card_id = str(getattr(response.data, "card_id", "") or "")
        if not card_id:
            raise RuntimeError("Feishu cardkit create returned no card_id")
        return card_id

    def update_element(
        self,
        card_id: str,
        element_id: str,
        content: str,
        sequence: int,
        *,
        uuid: str | None = None,
    ) -> None:
        """Typewriter-update one element. ``sequence`` must strictly increase.

        ``uuid`` is CardKit's idempotency key: a retry of the same logical update
        must pass the value it first sent, or the platform applies it twice.
        """
        request = (
            ContentCardElementRequest.builder()
            .card_id(card_id)
            .element_id(element_id)
            .request_body(
                ContentCardElementRequestBody.builder()
                .content(content)
                .sequence(sequence)
                .uuid(uuid or str(uuid4()))
                .build()
            )
            .build()
        )
        self._check(self._ensure_client().cardkit.v1.card_element.content(request), streaming=True)

    def close_streaming(self, card_id: str, sequence: int, *, uuid: str | None = None) -> None:
        """Close ``streaming_mode`` so the card stops accepting element updates.

        ``uuid`` follows ``update_element``; see there.
        """
        request = (
            SettingsCardRequest.builder()
            .card_id(card_id)
            .request_body(
                SettingsCardRequestBody.builder()
                .settings(json.dumps({"config": {"streaming_mode": False}}))
                .sequence(sequence)
                .uuid(uuid or str(uuid4()))
                .build()
            )
            .build()
        )
        self._check(self._ensure_client().cardkit.v1.card.settings(request), streaming=True)

    def send_card(self, chat_id: str, card_id: str, *, receive_id_type: str = "chat_id") -> str:
        """Send a message referencing an existing card; return its ``message_id``."""
        content = json.dumps({"type": "card", "data": {"card_id": card_id}})
        if self._reply_to_message_id:
            request = (
                ReplyMessageRequest.builder()
                .message_id(self._reply_to_message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(content)
                    .reply_in_thread(self._reply_in_thread)
                    .build()
                )
                .build()
            )
            response = self._ensure_client().im.v1.message.reply(request)
        else:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("interactive")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = self._ensure_client().im.v1.message.create(request)
        self._check(response, streaming=False)
        message_id = str(getattr(response.data, "message_id", "") or "")
        if not message_id:
            raise RuntimeError("Feishu message send returned no message_id")
        return message_id
