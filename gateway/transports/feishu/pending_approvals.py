"""Thread-safe server-side authority for Feishu approval card actions."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from gateway.core.middleware.approvals import MAX_APPROVAL_WAIT_SECONDS


class ClaimStatus(Enum):
    """Outcome of attempting to claim one approval action token."""

    CLAIMED = "claimed"
    SETTLED = "settled"
    IN_PROGRESS = "in_progress"
    WRONG_ACTOR = "wrong_actor"
    WRONG_CHAT = "wrong_chat"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ApprovalClaim:
    """Authority and decision returned for one token claim attempt."""

    status: ClaimStatus
    broker_approval_id: str = ""
    approved: bool | None = None
    tool_name: str = ""


@dataclass(frozen=True, slots=True)
class LegacyPendingApproval:
    """Temporary text-reply approval entry kept until card acceptance."""

    approval_id: str
    requester_open_id: str
    chat_id: str


class _RequestState(Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    SETTLED = "settled"


@dataclass(slots=True)
class _PendingRequest:
    broker_approval_id: str
    approve_token: str
    deny_token: str
    requester_open_id: str
    chat_id: str
    tool_name: str
    expires_at: float
    state: _RequestState = _RequestState.OPEN
    approved: bool | None = None
    settled_at: float | None = None


@dataclass(frozen=True, slots=True)
class _TokenBinding:
    request: _PendingRequest
    approved: bool


class PendingApprovals:
    """Map opaque action tokens to one shared approval request."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        retention_seconds: float = MAX_APPROVAL_WAIT_SECONDS,
    ) -> None:
        self._clock = clock
        self._retention_seconds = retention_seconds
        self._by_token: dict[str, _TokenBinding] = {}
        self._by_request: dict[str, _PendingRequest] = {}
        self._legacy_by_prompt: dict[str, LegacyPendingApproval] = {}
        self._lock = threading.Lock()

    def register(
        self,
        *,
        broker_approval_id: str,
        approve_token: str,
        deny_token: str,
        requester_open_id: str,
        chat_id: str,
        tool_name: str,
        expires_at: float,
    ) -> None:
        """Register both decision tokens before exposing their card."""
        if not approve_token or not deny_token or approve_token == deny_token:
            raise ValueError("approval action tokens must be non-empty and distinct")
        request = _PendingRequest(
            broker_approval_id=broker_approval_id,
            approve_token=approve_token,
            deny_token=deny_token,
            requester_open_id=requester_open_id,
            chat_id=chat_id,
            tool_name=tool_name,
            expires_at=expires_at,
        )
        with self._lock:
            self._cleanup_locked(self._clock())
            if broker_approval_id in self._by_request:
                raise ValueError("broker approval ID is already registered")
            if approve_token in self._by_token or deny_token in self._by_token:
                raise ValueError("approval action token is already registered")
            self._by_request[broker_approval_id] = request
            self._by_token[approve_token] = _TokenBinding(request=request, approved=True)
            self._by_token[deny_token] = _TokenBinding(request=request, approved=False)

    def claim(self, action_token: str, *, open_id: str, chat_id: str) -> ApprovalClaim:
        """Atomically claim a token when its requester and chat still match."""
        with self._lock:
            self._cleanup_locked(self._clock())
            binding = self._by_token.get(action_token)
            if binding is None:
                return ApprovalClaim(status=ClaimStatus.UNAVAILABLE)
            request = binding.request
            if request.requester_open_id != open_id:
                return ApprovalClaim(status=ClaimStatus.WRONG_ACTOR)
            if request.chat_id != chat_id:
                return ApprovalClaim(status=ClaimStatus.WRONG_CHAT)
            if request.state is _RequestState.SETTLED:
                return ApprovalClaim(
                    status=ClaimStatus.SETTLED,
                    broker_approval_id=request.broker_approval_id,
                    approved=request.approved,
                    tool_name=request.tool_name,
                )
            if request.state is _RequestState.CLAIMED:
                return ApprovalClaim(status=ClaimStatus.IN_PROGRESS)
            request.state = _RequestState.CLAIMED
            return ApprovalClaim(
                status=ClaimStatus.CLAIMED,
                broker_approval_id=request.broker_approval_id,
                approved=binding.approved,
                tool_name=request.tool_name,
            )

    def settle(self, broker_approval_id: str, *, approved: bool) -> bool:
        """Retain one claimed request as a bounded replay tombstone."""
        with self._lock:
            request = self._by_request.get(broker_approval_id)
            if request is None or request.state is not _RequestState.CLAIMED:
                return False
            request.state = _RequestState.SETTLED
            request.approved = approved
            request.settled_at = self._clock()
            return True

    def finish_wait(self, broker_approval_id: str) -> None:
        """Expire an unanswered request while preserving claim/settle races."""
        with self._lock:
            request = self._by_request.get(broker_approval_id)
            if request is not None and request.state is _RequestState.OPEN:
                self._remove_locked(request)

    def discard_request(self, broker_approval_id: str) -> bool:
        """Remove one request and both tokens after exposure failure."""
        with self._lock:
            request = self._by_request.get(broker_approval_id)
            if request is None:
                return False
            self._remove_locked(request)
            return True

    def register_legacy(
        self,
        prompt_message_id: str,
        *,
        approval_id: str,
        requester_open_id: str,
        chat_id: str,
    ) -> None:
        """Record a temporary text-reply prompt during the card migration."""
        with self._lock:
            self._legacy_by_prompt[prompt_message_id] = LegacyPendingApproval(
                approval_id=approval_id,
                requester_open_id=requester_open_id,
                chat_id=chat_id,
            )

    def find_legacy(self, prompt_message_id: str) -> LegacyPendingApproval | None:
        """Return a temporary text-reply prompt without consuming it."""
        with self._lock:
            return self._legacy_by_prompt.get(prompt_message_id)

    def claim_legacy(
        self, prompt_message_id: str, *, open_id: str, chat_id: str
    ) -> str | None:
        """Consume an authorized temporary text-reply prompt."""
        with self._lock:
            pending = self._legacy_by_prompt.get(prompt_message_id)
            if pending is None:
                return None
            if pending.requester_open_id != open_id or pending.chat_id != chat_id:
                return None
            del self._legacy_by_prompt[prompt_message_id]
            return pending.approval_id

    def discard_legacy(self, prompt_message_id: str) -> None:
        """Forget a temporary text-reply prompt after its wait finishes."""
        with self._lock:
            self._legacy_by_prompt.pop(prompt_message_id, None)

    def drain(self) -> list[str]:
        """Clear all live requests and tombstones, returning broker IDs once."""
        with self._lock:
            self._cleanup_locked(self._clock())
            approval_ids = list(self._by_request)
            approval_ids.extend(
                pending.approval_id for pending in self._legacy_by_prompt.values()
            )
            self._by_request.clear()
            self._by_token.clear()
            self._legacy_by_prompt.clear()
            return approval_ids

    def _cleanup_locked(self, now: float) -> None:
        for request in list(self._by_request.values()):
            if request.state is _RequestState.SETTLED:
                settled_at = request.settled_at
                expired = settled_at is not None and now >= settled_at + self._retention_seconds
            else:
                expired = now >= request.expires_at
            if expired:
                self._remove_locked(request)

    def _remove_locked(self, request: _PendingRequest) -> None:
        self._by_request.pop(request.broker_approval_id, None)
        self._by_token.pop(request.approve_token, None)
        self._by_token.pop(request.deny_token, None)


__all__ = [
    "ApprovalClaim",
    "ClaimStatus",
    "LegacyPendingApproval",
    "PendingApprovals",
]
