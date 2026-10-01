"""Pure public input normalization for bounded history search."""

from config.constants.feishu import (
    FEISHU_SEARCH_DEFAULT_LIMIT,
    FEISHU_SEARCH_MAX_QUERY_CHARS,
    FEISHU_SEARCH_MAX_RESULTS,
    FEISHU_SEARCH_MAX_WINDOW_SECONDS,
)
from integrations.feishu.search_types import (
    FeishuSearchError,
    FeishuSearchErrorCode,
    FeishuSearchInput,
)


def normalize_search_input(
    *,
    start_time: object,
    end_time: object,
    query: object = "",
    limit: object = FEISHU_SEARCH_DEFAULT_LIMIT,
    now: int,
) -> FeishuSearchInput:
    """Normalize a strictly typed search window and literal query."""
    if (
        not isinstance(start_time, int)
        or isinstance(start_time, bool)
        or not isinstance(end_time, int)
        or isinstance(end_time, bool)
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not isinstance(query, str)
    ):
        raise FeishuSearchError(FeishuSearchErrorCode.VALIDATION)
    if (
        not 0 <= start_time < end_time <= now
        or end_time - start_time > FEISHU_SEARCH_MAX_WINDOW_SECONDS
        or not 1 <= limit <= FEISHU_SEARCH_MAX_RESULTS
        or any(
            ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF
            for char in query
        )
    ):
        raise FeishuSearchError(FeishuSearchErrorCode.VALIDATION)
    normalized_query = query.strip()
    if len(normalized_query) > FEISHU_SEARCH_MAX_QUERY_CHARS:
        raise FeishuSearchError(FeishuSearchErrorCode.VALIDATION)
    return FeishuSearchInput(normalized_query, start_time, end_time, limit)
