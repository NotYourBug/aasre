"""Bounded sanitized JSON representation of one Feishu message body."""

import json
import math
from collections.abc import Collection
from typing import Any

from config.constants.feishu import (
    FEISHU_MESSAGE_READ_MAX_DEPTH,
    FEISHU_MESSAGE_READ_MAX_INPUT_BYTES,
    FEISHU_MESSAGE_READ_MAX_NODES,
    FEISHU_MESSAGE_READ_MAX_OUTPUT_CHARS,
)
from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.safety.masking.policy import MaskingPolicy
from infrastructure.safety.masking.rules import MaskingRules
from infrastructure.safety.masking.secrets import scrub_secrets
from integrations.feishu.read_types import (
    FeishuMessageContent,
    FeishuReadError,
    FeishuReadErrorCode,
)


def _valid_utf8(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _parse_body(raw: str) -> dict[str, Any]:
    code: FeishuReadErrorCode | None = None
    parsed: Any = None
    try:
        if len(raw.encode("utf-8")) > FEISHU_MESSAGE_READ_MAX_INPUT_BYTES:
            code = FeishuReadErrorCode.CONTENT_TOO_LARGE
        else:
            parsed = json.loads(raw)
    except RecursionError:
        code = FeishuReadErrorCode.CONTENT_TOO_COMPLEX
    except (ValueError, UnicodeError):
        code = FeishuReadErrorCode.UNSUPPORTED_CONTENT
    if code is not None:
        raise FeishuReadError(code)
    if not isinstance(parsed, dict):
        raise FeishuReadError(FeishuReadErrorCode.UNSUPPORTED_CONTENT)
    return parsed


def _validate_tree(body: dict[str, Any]) -> None:
    stack: list[tuple[Any, int]] = [(body, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > FEISHU_MESSAGE_READ_MAX_NODES or depth > FEISHU_MESSAGE_READ_MAX_DEPTH:
            raise FeishuReadError(FeishuReadErrorCode.CONTENT_TOO_COMPLEX)
        if isinstance(value, str) and not _valid_utf8(value):
            raise FeishuReadError(FeishuReadErrorCode.UNSUPPORTED_CONTENT)
        if isinstance(value, float) and not math.isfinite(value):
            raise FeishuReadError(FeishuReadErrorCode.UNSUPPORTED_CONTENT)
        children: Collection[Any]
        if isinstance(value, dict):
            if any(not _valid_utf8(key) for key in value):
                raise FeishuReadError(FeishuReadErrorCode.UNSUPPORTED_CONTENT)
            children = value.values()
        elif isinstance(value, list):
            children = value
        else:
            continue
        if nodes + len(stack) + len(children) > FEISHU_MESSAGE_READ_MAX_NODES:
            raise FeishuReadError(FeishuReadErrorCode.CONTENT_TOO_COMPLEX)
        stack.extend((child, depth + 1) for child in children)


def _scrub_strings(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_secrets(value)
    if isinstance(value, dict):
        return {key: _scrub_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_strings(item) for item in value]
    return value


def normalize_read_content(raw_content: str) -> FeishuMessageContent:
    """Sanitize bounded object JSON before one serialization and explicit prefix truncation."""
    if not isinstance(raw_content, str):
        raise FeishuReadError(FeishuReadErrorCode.UNSUPPORTED_CONTENT)
    if len(raw_content) > FEISHU_MESSAGE_READ_MAX_INPUT_BYTES:
        raise FeishuReadError(FeishuReadErrorCode.CONTENT_TOO_LARGE)
    original = _parse_body(raw_content)
    _validate_tree(original)
    sanitized = _scrub_strings(redact_sensitive(original))
    policy = MaskingPolicy.from_env()
    if policy.enabled:
        sanitized = MaskingRules(policy).mask_value(sanitized)
    redacted = sanitized != original
    serialized = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    truncated = len(serialized) > FEISHU_MESSAGE_READ_MAX_OUTPUT_CHARS
    return FeishuMessageContent(
        serialized[:FEISHU_MESSAGE_READ_MAX_OUTPUT_CHARS],
        "json_prefix" if truncated else "json",
        truncated,
        redacted,
    )
