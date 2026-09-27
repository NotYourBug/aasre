"""Exact-key routing between independent approval and feedback authorities."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from gateway.core.middleware.approvals import ApprovalBroker
from gateway.transports.feishu.approvals import handle_card_action as handle_approval
from gateway.transports.feishu.feedback import FeishuFeedbackService, feedback_toast
from gateway.transports.feishu.pending_approvals import PendingApprovals


def handle_card_action(
    data: P2CardActionTrigger,
    *,
    broker: ApprovalBroker,
    pending_approvals: PendingApprovals,
    env_allowed_open_ids: list[str],
    logger: logging.Logger,
    feedback: FeishuFeedbackService | None = None,
) -> P2CardActionTriggerResponse:
    """Dispatch known exact payloads and reject ambiguous authority claims."""
    event = data.event
    action = event.action if event else None
    value = action.value if action else None
    if not isinstance(value, Mapping):
        return feedback_toast("此操作暂不可用")
    keys = set(value)
    if keys == {"approval_id"}:
        return handle_approval(
            data,
            broker=broker,
            pending_approvals=pending_approvals,
            env_allowed_open_ids=env_allowed_open_ids,
            logger=logger,
        )
    if keys == {"feedback_id"} and feedback is not None:
        return feedback.handle_action(data)
    if keys & {"approval_id", "feedback_id"}:
        return feedback_toast("此操作暂不可用")
    return P2CardActionTriggerResponse()
