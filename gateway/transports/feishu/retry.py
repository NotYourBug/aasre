"""Bounded retry callback admission and current-authority checks."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from functools import partial

from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from config.constants.feishu import FEISHU_RETRY_QUEUE_LIMIT
from gateway.transports.feishu.feedback import feedback_toast
from gateway.transports.feishu.reply_actions import PreparedRetry, ReplyActionRegistry

logger = logging.getLogger(__name__)


class FeishuRetryService:
    """Reauthorize, claim, and dispatch one-shot retries off the callback thread."""

    def __init__(
        self,
        *,
        reply_actions: ReplyActionRegistry,
        authorized: Callable[[str, str], bool],
        current_session_id: Callable[[str, str], str | None],
        dispatch: Callable[[PreparedRetry], bool],
        queue_limit: int = FEISHU_RETRY_QUEUE_LIMIT,
    ) -> None:
        if queue_limit < 1:
            raise ValueError("queue_limit must be positive")
        self._reply_actions = reply_actions
        self._authorized = authorized
        self._current_session_id = current_session_id
        self._dispatch = dispatch
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="feishu-retry")
        self._permits = threading.BoundedSemaphore(queue_limit)
        self._lock = threading.Lock()
        self._futures: set[Future[None]] = set()
        self._closed = False

    def handle_action(self, data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        """Admit bounded metadata and immediately return a private receipt."""
        parsed = self._parse_action(data)
        if parsed is None:
            return feedback_toast("此操作暂不可用")
        token, actor, chat = parsed
        if not self._submit(partial(self._claim_and_dispatch, token, actor, chat)):
            return feedback_toast("暂时繁忙，请稍后重试")
        return feedback_toast("已收到")

    @staticmethod
    def _parse_action(data: P2CardActionTrigger) -> tuple[str, str, str] | None:
        event = data.event
        if event is None or event.operator is None or event.context is None:
            return None
        action = event.action
        if action is None or action.tag != "button" or not isinstance(action.value, Mapping):
            return None
        if set(action.value) != {"retry_id"}:
            return None
        token = action.value["retry_id"]
        actor = event.operator.open_id
        chat = event.context.open_chat_id
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 256
            for value in (token, actor, chat)
        ):
            return None
        return token, actor, chat

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

    def _claim_and_dispatch(self, token: str, actor: str, chat: str) -> None:
        try:
            if not self._authorized(actor, chat):
                return
            session_id = self._current_session_id(actor, chat)
            if not session_id:
                return
            prepared = self._reply_actions.claim_retry_token(
                token,
                actor_open_id=actor,
                chat_id=chat,
                current_session_id=session_id,
            )
            if prepared is None:
                return
            try:
                accepted = self._dispatch(prepared)
            except Exception:
                accepted = False
            if accepted:
                self._reply_actions.mark_retry_dispatched(prepared.generation_id)
            else:
                self._reply_actions.mark_retry_unavailable(prepared.generation_id)
        except Exception:
            logger.warning("Feishu retry processing unavailable")

    def shutdown(self, *, timeout_seconds: float) -> None:
        """Close admission and allow a bounded drain of accepted callbacks."""
        with self._lock:
            self._closed = True
            futures = tuple(self._futures)
            self._executor.shutdown(wait=False)
        if futures:
            wait(futures, timeout=max(0, timeout_seconds))


__all__ = ["FeishuRetryService"]
