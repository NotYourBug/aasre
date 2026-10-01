"""Immutable search records independent of SDK and tool encoding."""

from dataclasses import dataclass
from enum import StrEnum


class FeishuSearchErrorCode(StrEnum):
    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    CANCELLED = "cancelled"
    HISTORY_UNAVAILABLE = "history_unavailable"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"


class FeishuSearchError(Exception):
    """Carry only a safe search error category."""

    def __init__(self, code: FeishuSearchErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class FeishuSearchInput:
    query: str
    start_time: int
    end_time: int
    limit: int


class FeishuSearchStopReason(StrEnum):
    SOURCE_EXHAUSTED = "source_exhausted"
    PAGE_LIMIT = "page_limit"
    WORK_LIMIT = "work_limit"


@dataclass(frozen=True)
class FeishuSearchContent:
    text: str | None
    redacted: bool


@dataclass(frozen=True)
class FeishuSearchItem:
    message_id: str
    msg_type: str
    create_time_ms: int
    preview: str
    preview_available: bool
    preview_truncated: bool
    redacted: bool


@dataclass(frozen=True)
class FeishuSearchResult:
    chat_id: str
    start_time: int
    end_time: int
    items: tuple[FeishuSearchItem, ...]
    pages_fetched: int
    scanned_count: int
    matched_count: int
    unsearchable_count: int
    scan_complete: bool
    stop_reason: FeishuSearchStopReason
