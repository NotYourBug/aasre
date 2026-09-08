"""Feishu text-reply approval prompt for write tools.

Feishu has no interactive buttons, so a write tool that needs approval posts a
plain text prompt and waits for the requester to reply ``approve``/``deny`` to
it. The WS event handler (:mod:`gateway.transports.feishu.worker`) recognizes a
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

from gateway.core.middleware.approvals import (
    MAX_APPROVAL_WAIT_SECONDS,
    ApprovalBroker,
    arguments_preview,
)
from gateway.transports.feishu.pending_approvals import PendingApprovals

logger = logging.getLogger("gateway")


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
        self._pending_approvals.register(
            message_id,
            approval_id=approval_id,
            requester_open_id=self._requester_open_id,
            chat_id=self._chat_id,
        )
        try:
            timeout = min(float(expiry_seconds), MAX_APPROVAL_WAIT_SECONDS)
            approved, decided_by = self._broker.wait(approval_id, timeout=timeout)
        finally:
            self._pending_approvals.discard(message_id)
        return (approved, decided_by)


__all__ = ["FeishuApprovalPrompter"]
