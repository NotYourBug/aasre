"""Feishu CardKit approval prompts and callback resolution."""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Mapping
from typing import Any, Protocol

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
from integrations.feishu import render_approval_prompt_card, render_approval_result_card

logger = logging.getLogger("gateway")

_UNAVAILABLE = "This approval is unavailable"


class ApprovalCardClient(Protocol):
    """Card operations needed to expose one approval prompt."""

    def create_card(self, spec: dict[str, object]) -> str:
        """Create a card entity and return its card ID."""

    def send_card(
        self, chat_id: str, card_id: str, *, receive_id_type: str = "chat_id"
    ) -> str:
        """Expose a card in one chat and return its message ID."""


def _action_token() -> str:
    """Return one unguessable Feishu approval action token."""
    return secrets.token_urlsafe(32)


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
    """Expose a CardKit approval and wait for its authorized callback."""

    def __init__(
        self,
        *,
        broker: ApprovalBroker,
        card_client: ApprovalCardClient,
        chat_id: str,
        requester_open_id: str,
        pending_approvals: PendingApprovals,
    ) -> None:
        self._broker = broker
        self._card_client = card_client
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
        timeout = max(0.0, min(float(expiry_seconds), MAX_APPROVAL_WAIT_SECONDS))
        approval_id = self._broker.create(platform="feishu", chat_id=self._chat_id)
        registered = False
        try:
            approve_token = _action_token()
            deny_token = _action_token()
            spec = render_approval_prompt_card(
                approve_token=approve_token,
                deny_token=deny_token,
                tool_name=tool_name,
                reason=reason,
                arguments_preview=arguments_preview(arguments),
            )
            card_id = self._card_client.create_card(spec)
            if not card_id:
                raise RuntimeError("Feishu approval card creation returned no card ID")
            self._pending_approvals.register(
                broker_approval_id=approval_id,
                approve_token=approve_token,
                deny_token=deny_token,
                requester_open_id=self._requester_open_id,
                chat_id=self._chat_id,
                tool_name=tool_name,
                expires_at=time.monotonic() + timeout,
            )
            registered = True
            message_id = self._card_client.send_card(self._chat_id, card_id)
            if not message_id:
                raise RuntimeError("Feishu approval card send returned no message ID")
        except Exception:
            logger.warning(
                "[feishu-gateway] approval card exposure failed tool=%s chat=%s",
                tool_name,
                self._chat_id,
                exc_info=True,
            )
            if registered:
                self._pending_approvals.discard_request(approval_id)
            if not self._broker.abandon(approval_id):
                self._broker.wait(approval_id, timeout=0.0)
            return (False, "")
        try:
            approved, decided_by = self._broker.wait(approval_id, timeout=timeout)
        finally:
            self._pending_approvals.finish_wait(approval_id)
        return (approved, decided_by)


__all__ = ["ApprovalCardClient", "FeishuApprovalPrompter", "handle_card_action"]
