"""Public validation for bounded current-chat searches."""

import time
from typing import Any

from config.constants.feishu import FEISHU_SEARCH_DEFAULT_LIMIT
from integrations.feishu.read_scope import resolve_read_scope
from integrations.feishu.read_types import FeishuReadError
from integrations.feishu.search_input import normalize_search_input
from integrations.feishu.search_types import FeishuSearchError


def prepare_search_input(
    payload: dict[str, Any], resolved_integrations: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Validate public parameters and scope without loading credentials."""
    if not {"start_time", "end_time"} <= set(payload) or set(payload) - {
        "query",
        "start_time",
        "end_time",
        "limit",
    }:
        return {}, "Feishu search requires only query, start_time, end_time and limit"
    try:
        inputs = normalize_search_input(
            start_time=payload["start_time"],
            end_time=payload["end_time"],
            query=payload.get("query", ""),
            limit=payload.get("limit", FEISHU_SEARCH_DEFAULT_LIMIT),
            now=int(time.time()),
        )
        resolve_read_scope(resolved_integrations)
    except (FeishuSearchError, FeishuReadError):
        return {}, "Feishu search input or current-chat authority is invalid"
    return {
        "query": inputs.query,
        "start_time": inputs.start_time,
        "end_time": inputs.end_time,
        "limit": inputs.limit,
    }, None
