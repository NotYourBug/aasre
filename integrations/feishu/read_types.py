"""Immutable read contracts independent of SDK, parser and tool results."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class FeishuReadErrorCode(StrEnum):
    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    CANCELLED = "cancelled"
    MESSAGE_UNAVAILABLE = "message_unavailable"
    UNSUPPORTED_CONTENT = "unsupported_content"
    CONTENT_TOO_LARGE = "content_too_large"
    CONTENT_TOO_COMPLEX = "content_too_complex"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"


class FeishuReadError(Exception):
    """Carry only a safe read error category."""

    def __init__(self, code: FeishuReadErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class FeishuReadScope:
    app_id: str
    chat_id: str


@dataclass(frozen=True)
class FeishuMessageContent:
    body_content: str
    body_format: Literal["json", "json_prefix"]
    truncated: bool
    redacted: bool


@dataclass(frozen=True)
class FeishuReadMessage:
    message_id: str
    chat_id: str
    msg_type: str
    content: FeishuMessageContent
