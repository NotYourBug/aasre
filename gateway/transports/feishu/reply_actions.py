"""Bounded in-memory authority for Feishu answer actions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from config.constants.feishu import (
    FEISHU_REACTION_GOOD_EMOJI,
    FEISHU_REACTION_RETRY_EMOJI,
    FEISHU_REPLY_ACTION_MAX_ENTRIES,
    FEISHU_RETRY_EXPIRY_SECONDS,
)
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.session_rotation import conversation_key


class ReplyActionState(StrEnum):
    """Lifecycle of one answer generation's action authority."""

    PREPARING = "preparing"
    STREAMING = "streaming"
    READY = "ready"
    RETRY_CLAIMED = "retry_claimed"
    RETRY_DISPATCHED = "retry_dispatched"
    INVALIDATED = "invalidated"


class ReactionAction(StrEnum):
    """Business actions supported by Feishu reaction shortcuts."""

    GOOD = "good"
    RETRY = "retry"


class ReactionTransition(StrEnum):
    """Result of one ordered reaction observation."""

    IGNORED = "ignored"
    PENDING = "pending"
    READY = "ready"
    CANCELLED = "cancelled"
    EXECUTED = "executed"


@dataclass(frozen=True, slots=True)
class ReplyActionHandle:
    """Opaque values retained by the answer lifecycle coordinator."""

    generation_id: str
    retry_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ReplyActionCompletion:
    """Certain final target and any reaction intents awaiting execution."""

    generation_id: str
    final_message_id: str
    card_id: str
    next_sequence: int
    pending_actions: frozenset[ReactionAction]


@dataclass(frozen=True, slots=True)
class PreparedRetry:
    """Authorized memory-only retry capsule returned by an atomic claim."""

    generation_id: str
    conversation_key: str
    requester_open_id: str
    chat_id: str
    session_id: str
    prompt: str = field(repr=False)
    inbound: FeishuInboundMessage = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreparedGood:
    """Metadata required to write one reaction-backed good verdict."""

    generation_id: str
    final_message_id: str
    requester_open_id: str
    chat_id: str


@dataclass(frozen=True, slots=True)
class ReplyActionContext:
    """Non-content authority and pending actions for deferred processing."""

    generation_id: str
    conversation_key: str
    requester_open_id: str
    chat_id: str
    session_id: str
    pending_actions: frozenset[ReactionAction]


@dataclass(slots=True)
class _ReplyActionRecord:
    generation_id: str
    conversation_key: str
    requester_open_id: str
    chat_id: str
    session_id: str
    prompt: str = field(repr=False)
    inbound: FeishuInboundMessage = field(repr=False)
    retry_digest: str = field(repr=False)
    created_at: float
    expires_at: float
    state: ReplyActionState = ReplyActionState.PREPARING
    message_ids: set[str] = field(default_factory=set)
    final_message_id: str = ""
    card_id: str = ""
    next_sequence: int = 0
    reaction_times: dict[tuple[str, str, ReactionAction], int] = field(default_factory=dict)
    pending_good: bool = False
    pending_retry: bool = False
    good_claimed: bool = False
    good_cancelled: bool = False


def _new_opaque_value() -> str:
    return secrets.token_urlsafe(32)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ReplyActionRegistry:
    """Own latest-answer action state and O(1) server-side indexes."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] = _new_opaque_value,
        generation_factory: Callable[[], str] = _new_opaque_value,
        expiry_seconds: float = FEISHU_RETRY_EXPIRY_SECONDS,
        max_entries: int = FEISHU_REPLY_ACTION_MAX_ENTRIES,
    ) -> None:
        if expiry_seconds <= 0:
            raise ValueError("expiry_seconds must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._clock = clock
        self._token_factory = token_factory
        self._generation_factory = generation_factory
        self._expiry_seconds = expiry_seconds
        self._max_entries = max_entries
        self._records: OrderedDict[str, _ReplyActionRecord] = OrderedDict()
        self._conversation_index: dict[str, str] = {}
        self._message_index: dict[str, str] = {}
        self._token_index: dict[str, str] = {}
        self._lock = threading.Lock()
        self._closed = False

    def begin(
        self,
        inbound: FeishuInboundMessage,
        *,
        prompt: str,
        session_id: str,
    ) -> ReplyActionHandle | None:
        """Replace a conversation's latest generation with a new retry capsule."""
        if not prompt or not session_id:
            raise ValueError("prompt and session_id must be nonempty")
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            if self._closed:
                return None
            key = conversation_key(inbound)
            previous = self._conversation_index.get(key)
            if previous is not None:
                self._drop_locked(previous)
            while len(self._records) >= self._max_entries:
                oldest = next(iter(self._records))
                self._drop_locked(oldest)

            generation_id = self._generation_factory()
            retry_token = self._token_factory()
            digest = _token_digest(retry_token)
            if generation_id in self._records or digest in self._token_index:
                raise RuntimeError("Reply action identifier collision")
            record = _ReplyActionRecord(
                generation_id=generation_id,
                conversation_key=key,
                requester_open_id=inbound.open_id,
                chat_id=inbound.chat_id,
                session_id=session_id,
                prompt=prompt,
                inbound=inbound,
                retry_digest=digest,
                created_at=now,
                expires_at=now + self._expiry_seconds,
            )
            self._records[generation_id] = record
            self._conversation_index[key] = generation_id
            self._token_index[digest] = generation_id
            return ReplyActionHandle(generation_id=generation_id, retry_token=retry_token)

    def observe_message(self, generation_id: str, message_id: str) -> bool:
        """Index one confirmed interactive answer message."""
        if not message_id:
            return False
        with self._lock:
            self._prune_locked(self._clock())
            record = self._live_record_locked(generation_id)
            if record is None or record.state not in {
                ReplyActionState.PREPARING,
                ReplyActionState.STREAMING,
            }:
                return False
            owner = self._message_index.get(message_id)
            if owner is not None and owner != generation_id:
                return False
            record.message_ids.add(message_id)
            self._message_index[message_id] = generation_id
            record.state = ReplyActionState.STREAMING
            return True

    def update_prompt(self, generation_id: str, prompt: str) -> bool:
        """Replace a preparing generation's prompt with normalized content."""
        if not prompt:
            return False
        with self._lock:
            self._prune_locked(self._clock())
            record = self._live_record_locked(generation_id)
            if record is None or record.state not in {
                ReplyActionState.PREPARING,
                ReplyActionState.STREAMING,
            }:
                return False
            record.prompt = prompt
            return True

    def complete(
        self,
        generation_id: str,
        *,
        final_message_id: str,
        card_id: str,
        next_sequence: int,
    ) -> ReplyActionCompletion | None:
        """Mark certain interactive delivery ready and expose pending intents."""
        if not final_message_id or not card_id or next_sequence < 0:
            return None
        with self._lock:
            self._prune_locked(self._clock())
            record = self._live_record_locked(generation_id)
            if (
                record is None
                or record.state not in {ReplyActionState.PREPARING, ReplyActionState.STREAMING}
                or final_message_id not in record.message_ids
            ):
                return None
            record.final_message_id = final_message_id
            record.card_id = card_id
            record.next_sequence = next_sequence
            record.state = ReplyActionState.READY
            pending: set[ReactionAction] = set()
            if record.pending_good:
                pending.add(ReactionAction.GOOD)
            if record.pending_retry:
                pending.add(ReactionAction.RETRY)
            return ReplyActionCompletion(
                generation_id=generation_id,
                final_message_id=final_message_id,
                card_id=card_id,
                next_sequence=next_sequence,
                pending_actions=frozenset(pending),
            )

    def observe_reaction(
        self,
        *,
        message_id: str,
        actor_open_id: str,
        operator_type: str,
        emoji_type: str,
        action_time: int,
        created: bool,
    ) -> ReactionTransition:
        """Apply one newer reaction event to its server-owned generation."""
        action = self._reaction_action(emoji_type)
        if (
            action is None
            or operator_type != "user"
            or not message_id
            or not actor_open_id
            or isinstance(action_time, bool)
            or not isinstance(action_time, int)
            or action_time < 0
        ):
            return ReactionTransition.IGNORED
        with self._lock:
            self._prune_locked(self._clock())
            generation_id = self._message_index.get(message_id)
            record = self._live_record_locked(generation_id)
            if record is None or actor_open_id != record.requester_open_id:
                return ReactionTransition.IGNORED
            ordering_key = (message_id, actor_open_id, action)
            if action_time <= record.reaction_times.get(ordering_key, -1):
                return ReactionTransition.IGNORED
            record.reaction_times[ordering_key] = action_time

            executed = (
                record.good_claimed
                if action is ReactionAction.GOOD
                else record.state
                in {
                    ReplyActionState.RETRY_CLAIMED,
                    ReplyActionState.RETRY_DISPATCHED,
                }
            )
            if executed:
                if action is ReactionAction.GOOD:
                    record.good_cancelled = not created
                return ReactionTransition.EXECUTED
            if not created:
                self._set_pending(record, action, False)
                return ReactionTransition.CANCELLED

            self._set_pending(record, action, True)
            ready_states = {
                ReplyActionState.READY,
                ReplyActionState.RETRY_CLAIMED,
                ReplyActionState.RETRY_DISPATCHED,
            }
            return (
                ReactionTransition.READY
                if record.state in ready_states
                else ReactionTransition.PENDING
            )

    def claim_retry_token(
        self,
        token: str,
        *,
        actor_open_id: str,
        chat_id: str,
        current_session_id: str,
    ) -> PreparedRetry | None:
        """Atomically claim an authorized retry button token."""
        if not token:
            return None
        digest = _token_digest(token)
        with self._lock:
            self._prune_locked(self._clock())
            generation_id = self._token_index.get(digest)
            record = self._live_record_locked(generation_id)
            if record is None or not hmac.compare_digest(record.retry_digest, digest):
                return None
            if (
                actor_open_id != record.requester_open_id
                or chat_id != record.chat_id
                or current_session_id != record.session_id
            ):
                return None
            return self._claim_retry_locked(record)

    def claim_pending_retry(
        self, generation_id: str, *, current_session_id: str
    ) -> PreparedRetry | None:
        """Claim a pending CrossMark intent after successful completion."""
        with self._lock:
            self._prune_locked(self._clock())
            record = self._live_record_locked(generation_id)
            if (
                record is None
                or not record.pending_retry
                or current_session_id != record.session_id
            ):
                return None
            return self._claim_retry_locked(record)

    def claim_pending_good(
        self, generation_id: str, *, current_session_id: str
    ) -> PreparedGood | None:
        """Claim a pending THUMBSUP intent for durable S5b persistence."""
        with self._lock:
            self._prune_locked(self._clock())
            record = self._live_record_locked(generation_id)
            if (
                record is None
                or not record.pending_good
                or record.good_claimed
                or not record.final_message_id
                or current_session_id != record.session_id
                or record.state
                not in {
                    ReplyActionState.READY,
                    ReplyActionState.RETRY_CLAIMED,
                    ReplyActionState.RETRY_DISPATCHED,
                }
            ):
                return None
            record.pending_good = False
            record.good_claimed = True
            record.good_cancelled = False
            return PreparedGood(
                generation_id=record.generation_id,
                final_message_id=record.final_message_id,
                requester_open_id=record.requester_open_id,
                chat_id=record.chat_id,
            )

    def release_good_claim(self, generation_id: str) -> bool:
        """Rearm a good intent whose durable write was not accepted."""
        with self._lock:
            self._prune_locked(self._clock())
            record = self._live_record_locked(generation_id)
            if record is None or not record.good_claimed:
                return False
            record.good_claimed = False
            record.pending_good = not record.good_cancelled
            return True

    def is_current_retry_claim(self, generation_id: str, conversation: str) -> bool:
        """Return whether a claimed retry still owns its conversation."""
        with self._lock:
            self._prune_locked(self._clock())
            record = self._live_record_locked(generation_id)
            return (
                record is not None
                and record.conversation_key == conversation
                and self._conversation_index.get(conversation) == generation_id
                and record.state
                in {
                    ReplyActionState.RETRY_CLAIMED,
                    ReplyActionState.RETRY_DISPATCHED,
                }
            )

    def mark_retry_dispatched(self, generation_id: str) -> bool:
        """Record successful handoff of a claimed retry to turn dispatch."""
        with self._lock:
            self._prune_locked(self._clock())
            record = self._live_record_locked(generation_id)
            if record is None or record.state is not ReplyActionState.RETRY_CLAIMED:
                return False
            record.state = ReplyActionState.RETRY_DISPATCHED
            return True

    def mark_retry_unavailable(self, generation_id: str) -> bool:
        """Conservatively invalidate a claim that dispatch cannot accept."""
        return self.invalidate(generation_id)

    def invalidate(self, generation_id: str) -> bool:
        """Remove one generation and every server-owned index."""
        with self._lock:
            self._prune_locked(self._clock())
            return self._drop_locked(generation_id)

    def invalidate_conversation(self, key: str) -> bool:
        """Invalidate the current answer generation for one conversation."""
        with self._lock:
            self._prune_locked(self._clock())
            generation_id = self._conversation_index.get(key)
            return generation_id is not None and self._drop_locked(generation_id)

    def state(self, generation_id: str) -> ReplyActionState | None:
        """Return a non-sensitive lifecycle state for tests and orchestration."""
        with self._lock:
            self._prune_locked(self._clock())
            record = self._live_record_locked(generation_id)
            return record.state if record is not None else None

    def message_generation(self, message_id: str) -> str | None:
        """Resolve a server-observed CardKit message in O(1)."""
        with self._lock:
            self._prune_locked(self._clock())
            return self._message_index.get(message_id)

    def context_for_message(self, message_id: str) -> ReplyActionContext | None:
        """Return server-owned authority for a confirmed answer message."""
        with self._lock:
            self._prune_locked(self._clock())
            return self._context_locked(self._message_index.get(message_id))

    def context_for_conversation(self, key: str) -> ReplyActionContext | None:
        """Return the latest ready generation for deferred action processing."""
        with self._lock:
            self._prune_locked(self._clock())
            return self._context_locked(self._conversation_index.get(key))

    def debug_record(self, generation_id: str) -> str:
        """Return bounded non-sensitive state for diagnostics."""
        with self._lock:
            self._prune_locked(self._clock())
            record = self._live_record_locked(generation_id)
            if record is None:
                return "ReplyActionRecord(unavailable)"
            return (
                "ReplyActionRecord("
                f"state={record.state.value}, messages={len(record.message_ids)}, "
                f"pending_good={record.pending_good}, pending_retry={record.pending_retry})"
            )

    def close(self) -> None:
        """Close admission and forget every memory-only capsule."""
        with self._lock:
            self._closed = True
            self._records.clear()
            self._conversation_index.clear()
            self._message_index.clear()
            self._token_index.clear()

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked(self._clock())
            return len(self._records)

    @staticmethod
    def _reaction_action(emoji_type: str) -> ReactionAction | None:
        if emoji_type == FEISHU_REACTION_GOOD_EMOJI:
            return ReactionAction.GOOD
        if emoji_type == FEISHU_REACTION_RETRY_EMOJI:
            return ReactionAction.RETRY
        return None

    @staticmethod
    def _set_pending(record: _ReplyActionRecord, action: ReactionAction, pending: bool) -> None:
        if action is ReactionAction.GOOD:
            record.pending_good = pending
        else:
            record.pending_retry = pending

    def _claim_retry_locked(self, record: _ReplyActionRecord) -> PreparedRetry | None:
        if record.state is not ReplyActionState.READY:
            return None
        record.pending_retry = False
        record.state = ReplyActionState.RETRY_CLAIMED
        return PreparedRetry(
            generation_id=record.generation_id,
            conversation_key=record.conversation_key,
            requester_open_id=record.requester_open_id,
            chat_id=record.chat_id,
            session_id=record.session_id,
            prompt=record.prompt,
            inbound=record.inbound,
        )

    def _live_record_locked(self, generation_id: str | None) -> _ReplyActionRecord | None:
        if generation_id is None:
            return None
        record = self._records.get(generation_id)
        if record is None or record.state is ReplyActionState.INVALIDATED:
            return None
        return record

    def _context_locked(self, generation_id: str | None) -> ReplyActionContext | None:
        record = self._live_record_locked(generation_id)
        if record is None or record.state not in {
            ReplyActionState.READY,
            ReplyActionState.RETRY_CLAIMED,
            ReplyActionState.RETRY_DISPATCHED,
        }:
            return None
        pending: set[ReactionAction] = set()
        if record.pending_good and not record.good_claimed:
            pending.add(ReactionAction.GOOD)
        if record.pending_retry and record.state is ReplyActionState.READY:
            pending.add(ReactionAction.RETRY)
        return ReplyActionContext(
            generation_id=record.generation_id,
            conversation_key=record.conversation_key,
            requester_open_id=record.requester_open_id,
            chat_id=record.chat_id,
            session_id=record.session_id,
            pending_actions=frozenset(pending),
        )

    def _prune_locked(self, now: float) -> None:
        expired = [
            generation_id
            for generation_id, record in self._records.items()
            if record.expires_at <= now
        ]
        for generation_id in expired:
            self._drop_locked(generation_id)

    def _drop_locked(self, generation_id: str) -> bool:
        record = self._records.pop(generation_id, None)
        if record is None:
            return False
        record.state = ReplyActionState.INVALIDATED
        if self._conversation_index.get(record.conversation_key) == generation_id:
            self._conversation_index.pop(record.conversation_key, None)
        for message_id in record.message_ids:
            if self._message_index.get(message_id) == generation_id:
                self._message_index.pop(message_id, None)
        if self._token_index.get(record.retry_digest) == generation_id:
            self._token_index.pop(record.retry_digest, None)
        return True


__all__ = [
    "PreparedGood",
    "PreparedRetry",
    "ReactionAction",
    "ReactionTransition",
    "ReplyActionCompletion",
    "ReplyActionContext",
    "ReplyActionHandle",
    "ReplyActionRegistry",
    "ReplyActionState",
]
