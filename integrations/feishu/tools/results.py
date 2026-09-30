"""Safe, stable results for Feishu agent message writes."""

from __future__ import annotations

import json

from core.tool import ToolExecutionResult
from integrations.feishu.delivery_types import (
    FeishuDeliveryErrorCategory,
    FeishuDeliveryStatus,
)
from integrations.feishu.document_delivery import FeishuDocumentDeliveryResult

_DEFINITE_PLATFORM_REJECTIONS = frozenset(
    {
        FeishuDeliveryErrorCategory.AUTHORIZATION,
        FeishuDeliveryErrorCategory.VALIDATION,
        FeishuDeliveryErrorCategory.RATE_LIMIT,
        FeishuDeliveryErrorCategory.DEFINITE_REJECTION,
    }
)


def failed_result(
    *,
    target: str = "",
    reply_to_message_id: str = "",
    error_type: str,
    error: str,
    attempted: bool = False,
    certainty: str = "definitely_not_sent",
) -> ToolExecutionResult:
    """Return a safe failure result without raw provider detail."""
    payload = {
        "source": "feishu",
        "status": "failed",
        "sent": False,
        "attempted": attempted,
        "target": target,
        "reply_to_message_id": reply_to_message_id,
        "confirmed_message_ids": [],
        "delivery_mode": "none",
        "certainty": certainty,
        "retry_safe": not attempted and certainty == "definitely_not_sent",
        "error_type": error_type,
        "error": error,
    }
    return ToolExecutionResult(content=json.dumps(payload), details=payload, is_error=True)


def delivery_result(
    delivery: FeishuDocumentDeliveryResult,
    *,
    target: str,
    reply_to_message_id: str = "",
) -> ToolExecutionResult:
    """Map complete, partial and ambiguous delivery without declaring unsafe retries."""
    complete = delivery.successful
    partial = delivery.status is FeishuDeliveryStatus.PARTIAL
    ambiguous = delivery.error_category is FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN
    certainty = (
        "confirmed_sent"
        if complete
        else "maybe_sent"
        if ambiguous
        else "partial"
        if partial
        else "definitely_not_sent"
    )
    payload = {
        "source": "feishu",
        "status": "sent" if complete else "partial" if partial else "failed",
        "sent": complete,
        "attempted": delivery.attempted,
        "target": target,
        "reply_to_message_id": reply_to_message_id,
        "confirmed_message_ids": list(delivery.confirmed_message_ids),
        "delivery_mode": delivery.delivery_mode.value,
        "certainty": certainty,
        "retry_safe": (
            not complete
            and not partial
            and not ambiguous
            and (not delivery.attempted or delivery.error_category in _DEFINITE_PLATFORM_REJECTIONS)
        ),
        "error_type": delivery.error_category.value if delivery.error_category else "",
    }
    if not complete:
        payload["error"] = delivery.error
    return ToolExecutionResult(content=json.dumps(payload), details=payload, is_error=not complete)


__all__ = ["delivery_result", "failed_result"]
