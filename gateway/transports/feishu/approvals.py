"""Feishu text-reply approval prompt for write tools.

Write-tool approval remains a plain-text reply flow until the separate S5 card
interaction work replaces it. The requester replies ``approve``/``deny`` and
the WS event handler (:mod:`gateway.transports.feishu.worker`) recognizes a
reply to a pending approval prompt via its ``parent_id`` — shared through
:class:`gateway.transports.feishu.pending_approvals.PendingApprovals` — and
resolves it directly instead of starting a new turn.

The prompt is answerable only by the member whose turn raised it, in the chat
it was posted to — see
:class:`gateway.transports.feishu.pending_approvals.PendingApprovals`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from gateway.core.middleware.approvals import (
    MAX_APPROVAL_WAIT_SECONDS,
    ApprovalBroker,
    arguments_preview,
)
from gateway.transports.feishu.inbound_security import is_open_id_authorized
from gateway.transports.feishu.pending_approvals import ClaimStatus, PendingApprovals
from integrations.feishu import render_approval_result_card

logger = logging.getLogger("gateway")

_UNAVAILABLE = "This approval is unavailable"


def _toast(content: str, *, kind: str = "info") -> P2CardActionTriggerResponse:
    return P2CardActionTriggerResponse(
        {"toast": {"type": kind, "content": content}}
    )


def _settled_response(
    *, approved: bool, tool_name: str
) -> P2CardActionTriggerResponse:
    outcome = "Approved" if approved else "Denied"
    return P2CardActionTriggerResponse(
        {
            "toast": {"type": "success", "content": outcome},
            "card": {
                "type": "raw",
                "data": render_approval_result_card(
                    tool_name=tool_name, approved=approved
                ),
            },
        }
    )


def handle_card_action(
    data: P2CardActionTrigger,
    *,
    broker: ApprovalBroker,
    pending_approvals: PendingApprovals,
    env_allowed_open_ids: list[str],
    logger: logging.Logger,
) -> P2CardActionTriggerResponse:
    """Validate and resolve one Feishu S5a approval callback."""
    event = data.event
    if event is None or event.operator is None or event.context is None:
        return _toast(_UNAVAILABLE, kind="error")
    action = event.action
    if action is None or action.tag != "button":
        return _toast(_UNAVAILABLE, kind="error")
    value = action.value
    if not isinstance(value, Mapping):
        return _toast(_UNAVAILABLE, kind="error")
    if "approval_id" not in value:
        return P2CardActionTriggerResponse()
    if set(value) != {"approval_id"}:
        return _toast(_UNAVAILABLE, kind="error")
    action_token = value["approval_id"]
    if not isinstance(action_token, str) or not action_token.strip():
        return _toast(_UNAVAILABLE, kind="error")

    open_id = event.operator.open_id or ""
    chat_id = event.context.open_chat_id or ""
    if not open_id or not chat_id:
        return _toast(_UNAVAILABLE, kind="error")
    if not is_open_id_authorized(
        open_id=open_id,
        chat_id=chat_id,
        env_allowed_open_ids=env_allowed_open_ids,
    ):
        logger.warning(
            "[feishu-gateway] rejected approval callback from unauthorized member"
        )
        return _toast(_UNAVAILABLE, kind="error")

    claim = pending_approvals.claim(action_token, open_id=open_id, chat_id=chat_id)
    if claim.status is ClaimStatus.SETTLED:
        if claim.approved is None:
            return _toast(_UNAVAILABLE, kind="error")
        outcome = "approved" if claim.approved else "denied"
        return _toast(f"Already {outcome}")
    if claim.status is not ClaimStatus.CLAIMED or claim.approved is None:
        return _toast(_UNAVAILABLE, kind="error")
    if not broker.resolve(
        claim.broker_approval_id,
        approved=claim.approved,
        decided_by=open_id,
    ):
        return _toast(_UNAVAILABLE, kind="error")
    if not pending_approvals.settle(
        claim.broker_approval_id, approved=claim.approved
    ):
        logger.warning("[feishu-gateway] approval callback lost transport state")
        return _toast(_UNAVAILABLE, kind="error")
    return _settled_response(approved=claim.approved, tool_name=claim.tool_name)


class FeishuApprovalPrompter:
    """Posts a text approval prompt and waits for a reply resolving it.

    ``send_text`` posts one message to a chat and returns its ``message_id``
    (raising on transport failure); Feishu v1 has no in-place edit, so unlike
    the Buzz prompter there is no outcome edit — the decision is returned to
    :func:`gateway.core.middleware.approvals.approval_tool_hooks` directly.
    """

    def __init__(
        self,
        *,
        broker: ApprovalBroker,
        send_text: Callable[[str, str], str],
        chat_id: str,
        requester_open_id: str,
        pending_approvals: PendingApprovals,
    ) -> None:
        self._broker = broker
        self._send_text = send_text
        self._chat_id = chat_id
        self._requester_open_id = requester_open_id
        self._pending_approvals = pending_approvals

    def request(
        self,
        *,
        tool_name: str,
        reason: str,
        arguments: Mapping[str, Any],
        expiry_seconds: float,
    ) -> tuple[bool, str]:
        preview = arguments_preview(arguments)
        body = f"**Approval needed — `{tool_name}`**"
        if reason.strip():
            body += f"\n{reason.strip()}"
        if preview:
            body += f"\n```\n{preview}\n```"
        body += "\n\nReply **approve** or **deny** to this message. Only you can answer it."

        try:
            message_id = self._send_text(self._chat_id, body)
        except Exception:
            logger.warning(
                "[feishu-gateway] approval prompt post failed tool=%s chat=%s",
                tool_name,
                self._chat_id,
                exc_info=True,
            )
            return (False, "")
        if not message_id:
            return (False, "")

        # Create the broker entry only after the prompt is posted: a failed or
        # empty post must not leak an approval that ``wait`` never cleans up.
        approval_id = self._broker.create(platform="feishu", chat_id=self._chat_id)
        self._pending_approvals.register_legacy(
            message_id,
            approval_id=approval_id,
            requester_open_id=self._requester_open_id,
            chat_id=self._chat_id,
        )
        try:
            timeout = min(float(expiry_seconds), MAX_APPROVAL_WAIT_SECONDS)
            approved, decided_by = self._broker.wait(approval_id, timeout=timeout)
        finally:
            self._pending_approvals.discard_legacy(message_id)
        return (approved, decided_by)


__all__ = ["FeishuApprovalPrompter", "handle_card_action"]
