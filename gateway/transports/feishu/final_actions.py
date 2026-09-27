"""Coordinate durable adoption authority and in-memory retry visibility."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from functools import partial
from typing import Protocol
from uuid import uuid4

from config.constants.feishu import FEISHU_FEEDBACK_QUEUE_LIMIT
from gateway.transports.feishu.card_stream import FinalCardTarget
from gateway.transports.feishu.feedback import FeishuFeedbackService
from gateway.transports.feishu.reply_actions import (
    ReplyActionCompletion,
    ReplyActionHandle,
    ReplyActionRegistry,
)
from integrations.feishu import FeishuCardCallError, render_reply_action_elements

logger = logging.getLogger(__name__)


class ReplyActionCardClient(Protocol):
    def append_elements(
        self, card_id: str, elements: list[dict[str, object]], sequence: int, *, uuid: str
    ) -> None:
        """Append final reply actions after stream closure."""


class FinalActionCoordinator:
    """Publish one authorized adoption/retry action row per successful answer."""

    def __init__(
        self,
        *,
        feedback: FeishuFeedbackService,
        reply_actions: ReplyActionRegistry,
        card_client: ReplyActionCardClient,
        on_ready: Callable[[ReplyActionCompletion], None] | None = None,
        queue_limit: int = FEISHU_FEEDBACK_QUEUE_LIMIT,
    ) -> None:
        if queue_limit < 1:
            raise ValueError("queue_limit must be positive")
        self._feedback = feedback
        self._reply_actions = reply_actions
        self._card_client = card_client
        self._on_ready = on_ready
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="feishu-actions")
        self._permits = threading.BoundedSemaphore(queue_limit)
        self._lock = threading.Lock()
        self._futures: set[Future[None]] = set()
        self._closed = False

    def issue(
        self,
        handle: ReplyActionHandle,
        target: FinalCardTarget,
        *,
        requester_open_id: str,
        chat_id: str,
    ) -> bool:
        """Schedule one durable-authority registration and one component append."""
        completion = self._reply_actions.complete(
            handle.generation_id,
            final_message_id=target.message_id,
            card_id=target.card_id,
            next_sequence=target.append_sequence,
        )
        if completion is None:
            return False
        if self._on_ready is not None:
            try:
                self._on_ready(completion)
            except Exception:
                logger.warning("Feishu pending reply actions unavailable")
        return self._submit(partial(self._publish, handle, target, requester_open_id, chat_id))

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

    def _publish(
        self,
        handle: ReplyActionHandle,
        target: FinalCardTarget,
        requester_open_id: str,
        chat_id: str,
    ) -> None:
        try:
            feedback_token = self._feedback.register_authority(
                requester_open_id=requester_open_id,
                chat_id=chat_id,
                message_id=target.message_id,
            )
        except Exception:
            logger.warning("Feishu reply-action authority unavailable")
            return
        try:
            self._card_client.append_elements(
                target.card_id,
                render_reply_action_elements(
                    feedback_token=feedback_token,
                    retry_token=handle.retry_token,
                ),
                target.append_sequence,
                uuid=str(uuid4()),
            )
        except FeishuCardCallError:
            self._feedback.invalidate_authority(feedback_token)
            logger.warning("Feishu reply-action append rejected")
        except Exception:
            # An uncertain remote result may already have exposed the row.
            logger.warning("Feishu reply-action append uncertain")

    def shutdown(self, *, timeout_seconds: float) -> None:
        """Close admission and allow a bounded drain of accepted issuance work."""
        with self._lock:
            self._closed = True
            futures = tuple(self._futures)
            self._executor.shutdown(wait=False)
        if futures:
            wait(futures, timeout=max(0, timeout_seconds))


__all__ = ["FinalActionCoordinator"]
