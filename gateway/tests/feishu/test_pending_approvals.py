"""Authority checks for Feishu approval prompts awaiting a text reply."""

from __future__ import annotations

from gateway.transports.feishu.pending_approvals import PendingApprovals

REQUESTER = "ou_user-1"
OUTSIDER = "ou_user-2"
CHAT = "oc_chat-1"


def test_claim_returns_approval_id_for_requester_in_channel() -> None:
    pending = PendingApprovals()
    pending.register("prompt1", approval_id="a1", requester_open_id=REQUESTER, chat_id=CHAT)

    assert pending.find("prompt1") is not None
    assert pending.claim("prompt1", open_id=REQUESTER, chat_id=CHAT) == "a1"
    # Consumed by the successful claim.
    assert pending.find("prompt1") is None


def test_claim_rejects_another_member_without_consuming() -> None:
    pending = PendingApprovals()
    pending.register("prompt1", approval_id="a1", requester_open_id=REQUESTER, chat_id=CHAT)

    assert pending.claim("prompt1", open_id=OUTSIDER, chat_id=CHAT) is None
    # Not burned — the rightful requester can still answer.
    assert pending.find("prompt1") is not None


def test_claim_rejects_wrong_channel_without_consuming() -> None:
    pending = PendingApprovals()
    pending.register("prompt1", approval_id="a1", requester_open_id=REQUESTER, chat_id=CHAT)

    assert pending.claim("prompt1", open_id=REQUESTER, chat_id="oc_chat-2") is None
    assert pending.find("prompt1") is not None


def test_find_and_claim_return_none_for_unknown_prompt() -> None:
    pending = PendingApprovals()

    assert pending.find("missing") is None
    assert pending.claim("missing", open_id=REQUESTER, chat_id=CHAT) is None


def test_drain_returns_approval_ids_and_clears() -> None:
    pending = PendingApprovals()
    pending.register("prompt1", approval_id="a1", requester_open_id=REQUESTER, chat_id=CHAT)
    pending.register("prompt2", approval_id="a2", requester_open_id=REQUESTER, chat_id=CHAT)

    assert sorted(pending.drain()) == ["a1", "a2"]
    assert pending.find("prompt1") is None
    assert pending.find("prompt2") is None


def test_discard_forgets_the_prompt() -> None:
    pending = PendingApprovals()
    pending.register("prompt1", approval_id="a1", requester_open_id=REQUESTER, chat_id=CHAT)

    pending.discard("prompt1")
    assert pending.find("prompt1") is None
