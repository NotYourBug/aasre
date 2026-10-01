"""Safe text projection and literal matching for bounded history."""

import json
from typing import Any

from config.constants.feishu import (
    FEISHU_SEARCH_MAX_PREVIEW_CHARS,
    FEISHU_SEARCH_METADATA_KEYS,
    FEISHU_SEARCH_PREVIEW_LEAD_CHARS,
    FEISHU_SEARCH_RESOURCE_KEYS,
)
from integrations.feishu.message_content import normalize_read_content
from integrations.feishu.read_types import FeishuReadError
from integrations.feishu.search_types import FeishuSearchContent


def normalize_search_content(raw_content: str) -> FeishuSearchContent:
    """Project only complete sanitized JSON string values."""
    try:
        content = normalize_read_content(raw_content)
    except FeishuReadError:
        return FeishuSearchContent(None, False)
    if content.truncated or content.body_format != "json":
        return FeishuSearchContent(None, content.redacted)
    stack: list[Any] = [json.loads(content.body_content)]
    parts: list[str] = []
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            if value.get("tag") == "at":
                continue
            stack.extend(
                child
                for key, child in reversed(list(value.items()))
                if key not in FEISHU_SEARCH_RESOURCE_KEYS and key not in FEISHU_SEARCH_METADATA_KEYS
            )
        elif isinstance(value, list):
            stack.extend(reversed(value))
    return FeishuSearchContent("\n".join(parts), content.redacted)


def make_search_preview(content: FeishuSearchContent, query: str) -> tuple[str, bool] | None:
    """Return a bounded preview around a literal match in sanitized text."""
    text = content.text
    if text is None:
        return None
    start = 0
    if query:
        match = text.casefold().find(query.casefold())
        if match < 0:
            return None
        original_indices = [index for index, char in enumerate(text) for _ in char.casefold()]
        start = max(0, original_indices[match] - FEISHU_SEARCH_PREVIEW_LEAD_CHARS)
    end = min(len(text), start + FEISHU_SEARCH_MAX_PREVIEW_CHARS)
    return text[start:end], start > 0 or end < len(text)
