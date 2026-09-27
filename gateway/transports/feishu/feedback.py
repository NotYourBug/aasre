"""Final-card feedback issuance and bounded, metadata-only callback processing."""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from config.constants.feishu import FEISHU_FEEDBACK_QUEUE_LIMIT, FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS
from gateway.core.storage.feedback import FeedbackWriteResult, append_feedback_entry_once
from gateway.transports.feishu.feedback_authority import FeedbackAuthorityStore

logger = logging.getLogger(__name__)


def feedback_toast(content: str) -> P2CardActionTriggerResponse:
    """Return private status without modifying the shared card."""
    return P2CardActionTriggerResponse({"toast": {"type": "info", "content": content}})


class FeishuFeedbackService:
    """Authorize and persist feedback outside the WebSocket callback thread."""

    def __init__(
        self,
        *,
        authority: FeedbackAuthorityStore,
        feedback_path: Path,
        authorized: Callable[[str, str], bool],
        queue_limit: int = FEISHU_FEEDBACK_QUEUE_LIMIT,
    ) -> None:
        self._authority = authority
        self._feedback_path = feedback_path
        self._authorized = authorized
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="feishu-feedback")
        self._permits = threading.BoundedSemaphore(queue_limit)
        self._lock = threading.Lock()
        self._futures: set[Future[None]] = set()
        self._closed = False

    def _submit(self, work: Callable[[], None]) -> bool:
        with self._lock:
            if self._closed or not self._permits.acquire(blocking=False):
                return False
            try:
                future = self._executor.submit(work)
            except RuntimeError:
                self._permits.release()
                return False
            self._futures.add(future)

        def done(completed: Future[None]) -> None:
            with self._lock:
                self._futures.discard(completed)
                self._permits.release()

        future.add_done_callback(done)
        return True

    def register_authority(self, *, requester_open_id: str, chat_id: str, message_id: str) -> str:
        """Persist and return an opaque adoption capability before card visibility."""
        token = secrets.token_urlsafe(32)
        self._authority.register(
            token=token,
            requester_open_id=requester_open_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        return token

    def invalidate_authority(self, token: str) -> bool:
        """Revoke adoption authority after a definite component rejection."""
        return self._authority.invalidate(token)

    def record_good(self, *, requester_open_id: str, chat_id: str, message_id: str) -> bool:
        """Queue reaction-backed good feedback through the same durable writer."""
        return self._submit(partial(self._record_good, requester_open_id, chat_id, message_id))

    def handle_action(self, data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        """Admit a minimal callback receipt without waiting for disk or network."""
        event = data.event
        if event is None or event.operator is None or event.context is None:
            return feedback_toast("此操作暂不可用")
        action = event.action
        if action is None or action.tag != "button" or not isinstance(action.value, Mapping):
            return feedback_toast("此操作暂不可用")
        if set(action.value) != {"feedback_id"}:
            return feedback_toast("此操作暂不可用")
        token = action.value["feedback_id"]
        actor = event.operator.open_id
        chat = event.context.open_chat_id
        if (
            not isinstance(token, str)
            or not token.strip()
            or len(token) > 256
            or not isinstance(actor, str)
            or not actor
            or len(actor) > 256
            or not isinstance(chat, str)
            or not chat
            or len(chat) > 256
        ):
            return feedback_toast("此操作暂不可用")
        if not self._submit(partial(self._record, token, actor, chat)):
            return feedback_toast("暂时繁忙，请稍后重试")
        return feedback_toast("已收到")

    def _record(self, token: str, actor: str, chat: str) -> None:
        try:
            if not self._authorized(actor, chat):
                return
            authority = self._authority.find(token)
            if (
                authority is None
                or authority.requester_open_id != actor
                or authority.chat_id != chat
            ):
                return
            result = self._append_good(actor, authority.chat_id, authority.message_id)
            if result in {FeedbackWriteResult.WRITTEN, FeedbackWriteResult.DUPLICATE}:
                self._authority.consume(token)
        except Exception:
            logger.warning("Feishu feedback persistence unavailable")

    def _record_good(self, actor: str, chat: str, message_id: str) -> None:
        try:
            if not self._authorized(actor, chat):
                return
            self._append_good(actor, chat, message_id)
        except Exception:
            logger.warning("Feishu feedback persistence unavailable")

    def _append_good(self, actor: str, chat: str, message_id: str) -> FeedbackWriteResult:
        return append_feedback_entry_once(
            {
                "ts": datetime.now(UTC).isoformat(),
                "platform": "feishu",
                "user_id": actor,
                "chat_id": chat,
                "message_id": message_id,
                "verdict": "good",
            },
            idempotency_fields=("message_id", "user_id"),
            path=self._feedback_path,
            lock_timeout_seconds=FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS,
        )

    def shutdown(self, *, timeout_seconds: float) -> None:
        """Stop queue admission and allow a bounded drain of already received feedback."""
        with self._lock:
            self._closed = True
            futures = tuple(self._futures)
            self._executor.shutdown(wait=False)
        if futures:
            wait(futures, timeout=max(0, timeout_seconds))
