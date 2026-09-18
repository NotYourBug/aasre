"""Authority and replay semantics for Feishu card approval actions."""

from __future__ import annotations

import threading

import pytest

from gateway.core.middleware.approvals import MAX_APPROVAL_WAIT_SECONDS
from gateway.transports.feishu.pending_approvals import (
    ApprovalClaim,
    ClaimStatus,
    PendingApprovals,
)

REQUESTER = "ou_user-1"
OUTSIDER = "ou_user-2"
CHAT = "oc_chat-1"


class _Clock:
    def __init__(self, now: float = 10.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _register(pending: PendingApprovals, *, expires_at: float = 50.0) -> None:
    pending.register(
        broker_approval_id="broker-1",
        approve_token="token-approve",
        deny_token="token-deny",
        requester_open_id=REQUESTER,
        chat_id=CHAT,
        tool_name="write_tool",
        expires_at=expires_at,
    )


def _claim(pending: PendingApprovals, token: str = "token-approve") -> ApprovalClaim:
    return pending.claim(token, open_id=REQUESTER, chat_id=CHAT)


def test_tokens_bind_distinct_server_side_decisions() -> None:
    pending = PendingApprovals(clock=_Clock())
    _register(pending)

    claim = _claim(pending, "token-deny")

    assert claim == ApprovalClaim(
        status=ClaimStatus.CLAIMED,
        broker_approval_id="broker-1",
        approved=False,
        tool_name="write_tool",
    )
    assert _claim(pending, "token-approve").status is ClaimStatus.IN_PROGRESS


def test_wrong_actor_does_not_consume_either_token() -> None:
    pending = PendingApprovals(clock=_Clock())
    _register(pending)

    rejected = pending.claim("token-approve", open_id=OUTSIDER, chat_id=CHAT)
    accepted = _claim(pending, "token-deny")

    assert rejected.status is ClaimStatus.WRONG_ACTOR
    assert accepted.status is ClaimStatus.CLAIMED


def test_wrong_chat_does_not_consume_either_token() -> None:
    pending = PendingApprovals(clock=_Clock())
    _register(pending)

    rejected = pending.claim("token-approve", open_id=REQUESTER, chat_id="oc_other")
    accepted = _claim(pending)

    assert rejected.status is ClaimStatus.WRONG_CHAT
    assert accepted.status is ClaimStatus.CLAIMED


def test_unknown_and_expired_tokens_are_unavailable() -> None:
    clock = _Clock()
    pending = PendingApprovals(clock=clock)
    _register(pending, expires_at=clock.now)

    assert _claim(pending).status is ClaimStatus.UNAVAILABLE
    assert pending.claim("missing", open_id=REQUESTER, chat_id=CHAT).status is ClaimStatus.UNAVAILABLE
    assert pending.drain() == []


@pytest.mark.parametrize("_attempt", range(20))
def test_sibling_tokens_have_exactly_one_concurrent_winner(_attempt: int) -> None:
    pending = PendingApprovals(clock=_Clock())
    _register(pending)
    barrier = threading.Barrier(3)
    results: list[ApprovalClaim] = []
    lock = threading.Lock()

    def _race(token: str) -> None:
        barrier.wait(timeout=1.0)
        result = _claim(pending, token)
        with lock:
            results.append(result)

    threads = [
        threading.Thread(target=_race, args=(token,))
        for token in ("token-approve", "token-deny")
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=1.0)
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert [result.status for result in results].count(ClaimStatus.CLAIMED) == 1
    assert [result.status for result in results].count(ClaimStatus.IN_PROGRESS) == 1


def test_settled_result_is_shared_by_both_tokens_for_requester_only() -> None:
    pending = PendingApprovals(clock=_Clock())
    _register(pending)
    claim = _claim(pending)
    assert claim.approved is True

    assert pending.settle("broker-1", approved=True) is True
    approve_duplicate = _claim(pending, "token-approve")
    deny_duplicate = _claim(pending, "token-deny")
    outsider = pending.claim("token-approve", open_id=OUTSIDER, chat_id=CHAT)

    expected = ApprovalClaim(
        status=ClaimStatus.SETTLED,
        broker_approval_id="broker-1",
        approved=True,
        tool_name="write_tool",
    )
    assert approve_duplicate == expected
    assert deny_duplicate == expected
    assert outsider.status is ClaimStatus.WRONG_ACTOR


def test_settle_only_accepts_the_claimed_request_once() -> None:
    pending = PendingApprovals(clock=_Clock())
    _register(pending)

    assert pending.settle("broker-1", approved=True) is False
    _claim(pending)
    assert pending.settle("broker-1", approved=True) is True
    assert pending.settle("broker-1", approved=False) is False
    assert pending.settle("missing", approved=True) is False


def test_tombstone_expires_after_shared_retention_window() -> None:
    clock = _Clock()
    pending = PendingApprovals(clock=clock)
    _register(pending)
    _claim(pending)
    pending.settle("broker-1", approved=True)

    clock.now += MAX_APPROVAL_WAIT_SECONDS

    assert _claim(pending).status is ClaimStatus.UNAVAILABLE
    assert pending.drain() == []


def test_finish_wait_removes_open_but_preserves_claimed_and_settled() -> None:
    open_pending = PendingApprovals(clock=_Clock())
    _register(open_pending)
    open_pending.finish_wait("broker-1")
    assert _claim(open_pending).status is ClaimStatus.UNAVAILABLE

    claimed_pending = PendingApprovals(clock=_Clock())
    _register(claimed_pending)
    _claim(claimed_pending)
    claimed_pending.finish_wait("broker-1")
    assert claimed_pending.settle("broker-1", approved=True) is True
    assert _claim(claimed_pending).status is ClaimStatus.SETTLED


def test_discard_and_drain_remove_each_request_once() -> None:
    pending = PendingApprovals(clock=_Clock())
    _register(pending)
    pending.register(
        broker_approval_id="broker-2",
        approve_token="token-2-approve",
        deny_token="token-2-deny",
        requester_open_id=REQUESTER,
        chat_id=CHAT,
        tool_name="other_tool",
        expires_at=50.0,
    )

    assert pending.discard_request("broker-1") is True
    assert pending.discard_request("broker-1") is False
    assert pending.drain() == ["broker-2"]
    assert pending.drain() == []


def test_register_rejects_empty_identical_or_reused_authority_keys() -> None:
    pending = PendingApprovals(clock=_Clock())
    _register(pending)

    invalid = (
        {"broker_approval_id": "broker-empty", "approve_token": "", "deny_token": "deny"},
        {
            "broker_approval_id": "broker-identical",
            "approve_token": "same",
            "deny_token": "same",
        },
        {
            "broker_approval_id": "broker-1",
            "approve_token": "new-approve",
            "deny_token": "new-deny",
        },
        {
            "broker_approval_id": "broker-reused-token",
            "approve_token": "token-approve",
            "deny_token": "other-deny",
        },
    )
    for keys in invalid:
        with pytest.raises(ValueError):
            pending.register(
                requester_open_id=REQUESTER,
                chat_id=CHAT,
                tool_name="other_tool",
                expires_at=50.0,
                **keys,
            )

    assert _claim(pending).status is ClaimStatus.CLAIMED
