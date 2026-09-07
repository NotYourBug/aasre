"""Feishu approval prompter: posts a prompt, waits for a text reply."""

from __future__ import annotations

from gateway.core.middleware.approvals import ApprovalBroker, arguments_preview
from gateway.transports.feishu.approvals import FeishuApprovalPrompter
from gateway.transports.feishu.pending_approvals import PendingApprovals

REQUESTER = "ou_user-1"
CHAT = "oc_chat-1"


def _fake_send(posted: list[str], *, message_id: str = "prompt1", error: Exception | None = None):
    def _send(_chat_id: str, text: str) -> str:
        if error is not None:
            raise error
        posted.append(text)
        return message_id

    return _send


def test_request_posts_prompt_registers_and_resolves_on_approve() -> None:
    posted: list[str] = []
    broker = ApprovalBroker()
    pending = PendingApprovals()
    prompter = FeishuApprovalPrompter(
        broker=broker,
        send_text=_fake_send(posted),
        chat_id=CHAT,
        requester_open_id=REQUESTER,
        pending_approvals=pending,
    )

    # Simulate the WS handler resolving the approval before request() waits.
    def _resolve_soon() -> None:
        matched = pending.claim("prompt1", open_id=REQUESTER, chat_id=CHAT)
        assert matched is not None
        broker.resolve(matched, approved=True, decided_by=REQUESTER)

    original_wait = broker.wait

    def _wait_and_resolve(approval_id: str, *, timeout: float) -> tuple[bool, str]:
        _resolve_soon()
        return original_wait(approval_id, timeout=timeout)

    broker.wait = _wait_and_resolve  # type: ignore[method-assign]

    approved, decided_by = prompter.request(
        tool_name="feishu_send_message",
        reason="posting a summary",
        arguments={"api_key": "sk-should-not-appear", "channel": "opensre-test"},
        expiry_seconds=30,
    )

    assert approved is True
    assert decided_by == REQUESTER
    assert "sk-should-not-appear" not in posted[0]
    assert "opensre-test" in posted[0]
    # Cleaned up after resolution.
    assert pending.find("prompt1") is None


def test_request_returns_denied_when_prompt_post_fails() -> None:
    broker = ApprovalBroker()
    pending = PendingApprovals()
    prompter = FeishuApprovalPrompter(
        broker=broker,
        send_text=_fake_send([], error=RuntimeError("boom")),
        chat_id=CHAT,
        requester_open_id=REQUESTER,
        pending_approvals=pending,
    )

    approved, decided_by = prompter.request(
        tool_name="feishu_send_message", reason="", arguments={}, expiry_seconds=30
    )

    assert (approved, decided_by) == (False, "")
    assert pending.find("prompt1") is None


def test_request_returns_denied_when_prompt_post_returns_no_message_id() -> None:
    broker = ApprovalBroker()
    pending = PendingApprovals()
    prompter = FeishuApprovalPrompter(
        broker=broker,
        send_text=_fake_send([], message_id=""),
        chat_id=CHAT,
        requester_open_id=REQUESTER,
        pending_approvals=pending,
    )

    approved, decided_by = prompter.request(
        tool_name="feishu_send_message", reason="", arguments={}, expiry_seconds=30
    )

    assert (approved, decided_by) == (False, "")


def test_request_expiry_denies_and_discards() -> None:
    posted: list[str] = []
    broker = ApprovalBroker()
    pending = PendingApprovals()
    prompter = FeishuApprovalPrompter(
        broker=broker,
        send_text=_fake_send(posted),
        chat_id=CHAT,
        requester_open_id=REQUESTER,
        pending_approvals=pending,
    )

    approved, decided_by = prompter.request(
        tool_name="feishu_send_message", reason="", arguments={}, expiry_seconds=0.01
    )

    assert approved is False
    assert decided_by == ""
    assert pending.find("prompt1") is None


def test_arguments_preview_redacts_secrets() -> None:
    preview = arguments_preview({"api_key": "sk-secret-token", "channel": "opensre"})

    assert "sk-secret-token" not in preview
    assert "opensre" in preview


def test_arguments_preview_is_empty_for_no_arguments() -> None:
    assert arguments_preview({}) == ""
