"""Feishu turn output backed by one CardKit stream per turn."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.channel.outbound.streaming.markdown_stream import merge_streaming_text

from gateway.transports.feishu.card_stream import CardStreamSession, FinalCardTarget
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.delivery.notifications.redaction import redact_token
from infrastructure.turn_host.status_messages import (
    EMPTY_RESPONSE_MESSAGE,
    normalize_gateway_status,
    status_from_response_label,
    user_facing_error_message,
)
from integrations.feishu.card_client import FeishuCardClient

logger = logging.getLogger(__name__)


def _split_text(text: str, limit: int = MAX_MESSAGE_SIZE) -> list[str]:
    """Return ordered character-safe Feishu text chunks without dropping content."""
    if not text or limit <= 0:
        return []
    return [text[start : start + limit] for start in range(0, len(text), limit)]


def _send_text(
    app_id: str,
    app_secret: str,
    receive_id: str,
    text: str,
    *,
    reply_to_message_id: str = "",
    reply_in_thread: bool = False,
) -> str:
    """Send one text message or reply; return its ``message_id``."""
    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
    if reply_to_message_id:
        request = (
            ReplyMessageRequest.builder()
            .message_id(reply_to_message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .reply_in_thread(reply_in_thread)
                .build()
            )
            .build()
        )
        send = client.im.v1.message.reply
    else:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        send = client.im.v1.message.create
    try:
        response = send(request)
    except Exception:
        logger.exception("Feishu turn output send failed")
        raise
    if not response.success():
        error = redact_token(str(getattr(response, "msg", "") or ""), app_secret)
        logger.warning(
            "Feishu turn output send rejected code=%s: %s",
            getattr(response, "code", None),
            error,
        )
        raise RuntimeError(f"Feishu message send failed: {error}")
    data = getattr(response, "data", None)
    return str(getattr(data, "message_id", "") or "") if data is not None else ""


class FeishuTurnOutputRegistry:
    """Thread-safe ownership of active outputs for cooperative gateway shutdown."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._outputs: set[FeishuTurnOutput] = set()
        self._closed = False

    def register(self, output: FeishuTurnOutput) -> None:
        with self._lock:
            if not self._closed:
                self._outputs.add(output)
                return
        output.shutdown()

    def discard(self, output: FeishuTurnOutput) -> None:
        with self._lock:
            self._outputs.discard(output)

    def shutdown(self) -> None:
        """Stop and finish every output present at shutdown, exactly once."""
        with self._lock:
            self._closed = True
            outputs = tuple(self._outputs)
        for output in outputs:
            output.shutdown()


class FeishuTurnOutput:
    """Stream one turn through one CardKit session with complete text fallback."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        chat_id: str,
        reply_to_message_id: str = "",
        reply_in_thread: bool = False,
        edit_interval_seconds: float = 1.5,
        tool_hooks: object | None = None,
        turn_cancel: threading.Event | None = None,
        output_registry: FeishuTurnOutputRegistry | None = None,
    ) -> None:
        self.tool_hooks = tool_hooks
        self.turn_cancel = turn_cancel
        self._app_id = app_id
        self._app_secret = app_secret
        self._chat_id = chat_id
        self._reply_to_message_id = reply_to_message_id
        self._reply_in_thread = reply_in_thread
        self._edit_interval = edit_interval_seconds
        self._registry = output_registry
        self._lock = threading.RLock()
        self._session: CardStreamSession | None = None
        self._answer = ""
        self._terminal = False
        self._plain_fallback = False
        self._feedback_target: FinalCardTarget | None = None
        self._feedback_disqualified = False
        if output_registry is not None:
            output_registry.register(self)

    def print(self, message: str = "") -> None:
        if message:
            self._show_status(message)

    def render_response_header(self, label: str) -> None:
        self._show_status(status_from_response_label(label))

    def render_error(self, message: str) -> None:
        self.disqualify_feedback()
        logger.warning("gateway turn error chat=%s: %s", self._chat_id, message)
        self._complete(user_facing_error_message(message))

    def set_tool_status(self, status: str) -> None:
        self._show_status(normalize_gateway_status(status))

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with)
        for chunk in chunks:
            with self._lock:
                if self._terminal:
                    break
                self._answer = merge_streaming_text(self._answer, str(chunk))
                if self._answer.strip():
                    self._update_card(self._answer)
        text = self._answer
        if not defer_want_me_to_closer:
            self._complete(text or EMPTY_RESPONSE_MESSAGE)
        return text

    def finish_streamed_response(self, answer: str) -> None:
        self._complete(answer or EMPTY_RESPONSE_MESSAGE)

    def finalize(self, answer: str) -> None:
        self._complete(answer)

    def disqualify_feedback(self) -> None:
        """Prevent a non-success terminal path from exposing feedback."""
        with self._lock:
            self._feedback_disqualified = True
            self._feedback_target = None

    def take_feedback_target(self) -> FinalCardTarget | None:
        """Consume a complete target once after the handler's success claim."""
        with self._lock:
            if self._feedback_disqualified or (
                self.turn_cancel is not None and self.turn_cancel.is_set()
            ):
                return None
            target, self._feedback_target = self._feedback_target, None
            return target

    def shutdown(self) -> None:
        """Cancel the turn and close an already-created card without new output."""
        with self._lock:
            self._feedback_disqualified = True
            self._feedback_target = None
            if self._terminal:
                return
            self._terminal = True
            cancel = self.turn_cancel
            if isinstance(cancel, threading.Event):
                cancel.set()
            try:
                if self._session is not None:
                    self._session.finish()
            finally:
                self._discard_registry()

    def _show_status(self, status: str) -> None:
        with self._lock:
            if self._terminal or self._plain_fallback or not status.strip():
                return
            self._update_card(status)

    def _update_card(self, text: str) -> None:
        if self._plain_fallback:
            return
        session = self._session
        if session is None:
            client = FeishuCardClient(
                self._app_id,
                self._app_secret,
                reply_to_message_id=self._reply_to_message_id,
                reply_in_thread=self._reply_in_thread,
            )
            session = CardStreamSession(
                client=client,
                chat_id=self._chat_id,
                min_interval=self._edit_interval,
            )
            try:
                session.start()
            except Exception:
                logger.exception("Feishu streaming card could not be started; using text fallback")
                self._plain_fallback = True
                return
            self._session = session
        session.update(text)

    def _complete(self, answer: str) -> None:
        with self._lock:
            if self._terminal:
                return
            self._answer = merge_streaming_text(self._answer, answer)
            self._terminal = True
            try:
                if self._answer.strip():
                    self._update_card(self._answer)
                if self._session is not None:
                    self._feedback_target = self._session.finish()
                elif self._plain_fallback and self._answer:
                    self._send_plain_chunks(self._answer)
            finally:
                self._discard_registry()

    def _send_plain_chunks(self, text: str) -> None:
        for chunk in _split_text(text):
            _send_text(
                self._app_id,
                self._app_secret,
                self._chat_id,
                chunk,
                reply_to_message_id=self._reply_to_message_id,
                reply_in_thread=self._reply_in_thread,
            )

    def _discard_registry(self) -> None:
        if self._registry is not None:
            self._registry.discard(self)


__all__ = ["FeishuTurnOutput", "FeishuTurnOutputRegistry"]
