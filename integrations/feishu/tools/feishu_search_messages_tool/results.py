"""Bounded safe JSON search results."""

import json
from typing import Any

from config.constants.feishu import FEISHU_SEARCH_MAX_OUTPUT_CHARS, FEISHU_SEARCH_MAX_RESULTS
from core.tool import ToolExecutionResult
from integrations.feishu.search_types import FeishuSearchErrorCode, FeishuSearchResult

_ERRORS = {
    FeishuSearchErrorCode.VALIDATION: "Feishu search input is invalid",
    FeishuSearchErrorCode.AUTHORIZATION: "Feishu search is unavailable in this current chat",
    FeishuSearchErrorCode.CANCELLED: "Feishu search was cancelled",
    FeishuSearchErrorCode.HISTORY_UNAVAILABLE: "Feishu history is unavailable in this current chat",
    FeishuSearchErrorCode.RATE_LIMITED: "Feishu search was rate limited; try again later",
    FeishuSearchErrorCode.UPSTREAM_ERROR: "Feishu search could not be completed",
}


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def search_success(result: FeishuSearchResult) -> ToolExecutionResult:
    """Release whole safe items within the serialized output budget."""
    payload: dict[str, Any] = {
        "source": "feishu",
        "status": "searched",
        "search_mode": "bounded_chat_history",
        "chat_id": result.chat_id,
        "start_time": result.start_time,
        "end_time": result.end_time,
        "coverage": "current_chat_container",
        "thread_replies_included": False,
        "resource_content_included": False,
        "content_trust": "untrusted",
        "pages_fetched": result.pages_fetched,
        "scanned_count": result.scanned_count,
        "matched_count": result.matched_count,
        "unsearchable_count": result.unsearchable_count,
        "scan_complete": result.scan_complete,
        "content_complete": result.unsearchable_count == 0,
        "stop_reason": result.stop_reason.value,
    }
    prefix = "{" + _encode(payload)[1:-1] + ',"items":['
    reserve = (
        "],"
        + _encode(
            {
                "returned_count": FEISHU_SEARCH_MAX_RESULTS,
                "results_truncated": False,
                "complete": False,
            }
        )[1:-1]
        + "}"
    )
    used = len(prefix) + len(reserve)
    if used > FEISHU_SEARCH_MAX_OUTPUT_CHARS:
        return search_failure(FeishuSearchErrorCode.UPSTREAM_ERROR)
    selected: list[dict[str, Any]] = []
    encoded: list[str] = []
    for item in result.items:
        safe_item = {
            "message_id": item.message_id,
            "msg_type": item.msg_type,
            "create_time_ms": item.create_time_ms,
            "preview": item.preview,
            "preview_available": item.preview_available,
            "preview_truncated": item.preview_truncated,
            "redacted": item.redacted,
        }
        part = _encode(safe_item)
        required = len(part) + bool(encoded)
        if used + required > FEISHU_SEARCH_MAX_OUTPUT_CHARS:
            break
        used += required
        selected.append(safe_item)
        encoded.append(part)
    truncated = result.matched_count > len(selected)
    footer = {
        "returned_count": len(selected),
        "results_truncated": truncated,
        "complete": result.scan_complete and payload["content_complete"] and not truncated,
    }
    content = prefix + ",".join(encoded) + "]," + _encode(footer)[1:-1] + "}"
    payload["items"] = selected
    payload.update(footer)
    return ToolExecutionResult(content=content, details=payload)


def search_failure(code: FeishuSearchErrorCode) -> ToolExecutionResult:
    """Return a stable failure without upstream identity or exception details."""
    payload = {
        "source": "feishu",
        "status": "failed",
        "error_type": code.value,
        "error": _ERRORS[code],
    }
    return ToolExecutionResult(content=_encode(payload), details=payload, is_error=True)
