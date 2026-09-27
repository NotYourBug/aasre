"""Normalize official Feishu message-reaction events."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import partial

from lark_oapi.api.im.v1 import (
    P2ImMessageReactionCreatedV1,
    P2ImMessageReactionDeletedV1,
)

from config.constants.feishu import FEISHU_RETRY_QUEUE_LIMIT
from gateway.transports.feishu.feedback import FeishuFeedbackService
from gateway.transports.feishu.reply_actions import (
    PreparedRetry,
    ReactionAction,
    ReactionTransition,
    ReplyActionContext,
    ReplyActionRegistry,
)

_MAX_EVENT_FIELD_CHARS = 256
_MAX_ACTION_TIME_CHARS = 32
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FeishuReactionEvent:
    """Bounded reaction metadata safe to pass beyond the SDK callback."""

    event_id: str
    message_id: str
    actor_open_id: str
    operator_type: str
    emoji_type: str
    action_time: int
    created: bool


def _bounded(value: object, *, limit: int = _MAX_EVENT_FIELD_CHARS) -> str:
    return value if isinstance(value, str) and 0 < len(value) <= limit else ""


def _normalize(
    data: P2ImMessageReactionCreatedV1 | P2ImMessageReactionDeletedV1,
    *,
    created: bool,
) -> FeishuReactionEvent | None:
    header = data.header
    event = data.event
    if header is None or event is None:
        return None
    user_id = event.user_id
    reaction_type = event.reaction_type
    if user_id is None or reaction_type is None:
        return None
    event_id = _bounded(header.event_id)
    message_id = _bounded(event.message_id)
    actor_open_id = _bounded(user_id.open_id)
    operator_type = _bounded(event.operator_type)
    emoji_type = _bounded(reaction_type.emoji_type)
    raw_action_time = _bounded(event.action_time, limit=_MAX_ACTION_TIME_CHARS)
    if (
        not event_id
        or not message_id
        or not actor_open_id
        or operator_type != "user"
        or not emoji_type
        or not raw_action_time
    ):
        return None
    try:
        action_time = int(raw_action_time)
    except ValueError:
        return None
    if action_time < 0:
        return None
    return FeishuReactionEvent(
        event_id=event_id,
        message_id=message_id,
        actor_open_id=actor_open_id,
        operator_type=operator_type,
        emoji_type=emoji_type,
        action_time=action_time,
        created=created,
    )


def normalize_reaction_created(
    data: P2ImMessageReactionCreatedV1,
) -> FeishuReactionEvent | None:
    """Return a bounded created-reaction observation or ``None``."""
    return _normalize(data, created=True)


def normalize_reaction_deleted(
    data: P2ImMessageReactionDeletedV1,
) -> FeishuReactionEvent | None:
    """Return a bounded deleted-reaction observation or ``None``."""
    return _normalize(data, created=False)


class FeishuReactionService:
    """Apply ordered events inline and execute ready actions off the WS thread."""

    def __init__(
        self,
        *,
        reply_actions: ReplyActionRegistry,
        feedback: FeishuFeedbackService,
        authorized: Callable[[str, str], bool],
        current_session_id: Callable[[str, str], str | None],
        dispatch_retry: Callable[[PreparedRetry], bool],
        queue_limit: int = FEISHU_RETRY_QUEUE_LIMIT,
    ) -> None:
        self._reply_actions = reply_actions
        self._feedback = feedback
        self._authorized = authorized
        self._current_session_id = current_session_id
        self._dispatch_retry = dispatch_retry
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="feishu-reaction")
        self._permits = threading.BoundedSemaphore(queue_limit)
        self._lock = threading.Lock()
        self._futures: set[Future[None]] = set()
        self._closed = False

    def handle(self, event: FeishuReactionEvent) -> None:
        """Record one event and queue a ready business action without turn work inline."""
        transition = self._reply_actions.observe_reaction(
            message_id=event.message_id,
            actor_open_id=event.actor_open_id,
            operator_type=event.operator_type,
            emoji_type=event.emoji_type,
            action_time=event.action_time,
            created=event.created,
        )
        if transition is not ReactionTransition.READY:
            return
        context = self._reply_actions.context_for_message(event.message_id)
        if context is not None:
            self._submit_actions(context)

    def handle_pending(self, conversation: str) -> None:
        """Queue streaming-time intents after the source turn releases its slot."""
        context = self._reply_actions.context_for_conversation(conversation)
        if context is not None and context.pending_actions:
            self._submit_actions(context)

    def _submit_actions(self, context: ReplyActionContext) -> None:
        for action in context.pending_actions:
            self._submit(partial(self._execute, context, action))

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

    def _execute(self, context: ReplyActionContext, action: ReactionAction) -> None:
        try:
            if not self._authorized(context.requester_open_id, context.chat_id):
                return
            current = self._current_session_id(context.requester_open_id, context.chat_id)
            if not current or current != context.session_id:
                return
            if action is ReactionAction.GOOD:
                claimed = self._reply_actions.claim_pending_good(
                    context.generation_id, current_session_id=current
                )
                if claimed is not None:
                    self._feedback.record_good(
                        requester_open_id=claimed.requester_open_id,
                        chat_id=claimed.chat_id,
                        message_id=claimed.final_message_id,
                    )
                return
            prepared = self._reply_actions.claim_pending_retry(
                context.generation_id, current_session_id=current
            )
            if prepared is None:
                return
            if self._dispatch_retry(prepared):
                self._reply_actions.mark_retry_dispatched(prepared.generation_id)
            else:
                self._reply_actions.mark_retry_unavailable(prepared.generation_id)
        except Exception:
            logger.warning("Feishu reaction action unavailable")

    def shutdown(self, *, timeout_seconds: float) -> None:
        """Close admission and boundedly drain already accepted actions."""
        with self._lock:
            self._closed = True
            futures = tuple(self._futures)
            self._executor.shutdown(wait=False)
        if futures:
            wait(futures, timeout=max(0, timeout_seconds))


__all__ = [
    "FeishuReactionEvent",
    "FeishuReactionService",
    "normalize_reaction_created",
    "normalize_reaction_deleted",
]
