"""Safe immutable contracts for Feishu document delivery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from config.constants import (
    FEISHU_AUTHORIZATION_ERROR_CODES,
    FEISHU_RATE_LIMIT_ERROR_CODES,
    FEISHU_VALIDATION_ERROR_CODES,
)


class FeishuDeliveryStatus(StrEnum):
    SUCCESS = "success"
    DEGRADED_SUCCESS = "degraded_success"
    FAILED = "failed"
    SKIPPED = "skipped"


class FeishuDeliveryMode(StrEnum):
    CARDS = "cards"
    TEXT_FALLBACK = "text_fallback"
    NONE = "none"


class FeishuDeliveryErrorCategory(StrEnum):
    CONFIGURATION = "configuration"
    AUTHORIZATION = "authorization"
    VALIDATION = "validation"
    RATE_LIMIT = "rate_limit"
    DEFINITE_REJECTION = "definite_rejection"
    DELIVERY_UNCERTAIN = "delivery_uncertain"
    TRANSPORT = "transport"
    INTERNAL = "internal"


class FeishuSendCertainty(StrEnum):
    CONFIRMED_SENT = "confirmed_sent"
    DEFINITELY_NOT_SENT = "definitely_not_sent"
    MAYBE_SENT = "maybe_sent"


class FeishuCardCallStage(StrEnum):
    CREATE_CARD = "create_card"
    SEND_CARD = "send_card"
    UPDATE_ELEMENT = "update_element"
    CLOSE_STREAM = "close_stream"
    APPEND_ELEMENTS = "append_elements"


class FeishuCardCallError(RuntimeError):
    """A CardKit rejection containing only stable allowlisted fields."""

    def __init__(self, *, code: int, stage: FeishuCardCallStage) -> None:
        super().__init__(f"Feishu card call rejected during {stage.value}")
        self.code = code
        self.stage = stage


@dataclass(frozen=True)
class FeishuMessageSendResult:
    accepted: bool
    message_id: str
    error_category: FeishuDeliveryErrorCategory | None
    certainty: FeishuSendCertainty


def classify_feishu_rejection(code: int) -> FeishuDeliveryErrorCategory:
    """Classify a rejected Feishu call from a verified numeric code only."""
    if code in FEISHU_RATE_LIMIT_ERROR_CODES:
        return FeishuDeliveryErrorCategory.RATE_LIMIT
    if code in FEISHU_VALIDATION_ERROR_CODES:
        return FeishuDeliveryErrorCategory.VALIDATION
    if code in FEISHU_AUTHORIZATION_ERROR_CODES:
        return FeishuDeliveryErrorCategory.AUTHORIZATION
    return FeishuDeliveryErrorCategory.DEFINITE_REJECTION


__all__ = [
    "FeishuCardCallError",
    "FeishuCardCallStage",
    "FeishuDeliveryErrorCategory",
    "FeishuDeliveryMode",
    "FeishuDeliveryStatus",
    "FeishuMessageSendResult",
    "FeishuSendCertainty",
    "classify_feishu_rejection",
]
