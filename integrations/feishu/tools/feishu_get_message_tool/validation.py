"""Public validation for current-chat read requests."""

from typing import Any

from config.constants.feishu import FEISHU_MESSAGE_READ_MAX_ID_CHARS
from integrations.feishu.read_scope import resolve_read_scope
from integrations.feishu.read_types import FeishuReadError, FeishuReadErrorCode


def normalize_message_id(value: object) -> str:
    """Trim and validate a known message ID without coercion."""
    if not isinstance(value, str):
        raise FeishuReadError(FeishuReadErrorCode.VALIDATION)
    message_id = value.strip()
    if not message_id or len(message_id) > FEISHU_MESSAGE_READ_MAX_ID_CHARS:
        raise FeishuReadError(FeishuReadErrorCode.VALIDATION)
    return message_id


def prepare_read_input(
    payload: dict[str, Any], resolved_integrations: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Validate one public ID and the current integration scope."""
    if set(payload) != {"message_id"}:
        return {}, "Feishu read requires only message_id"
    try:
        message_id = normalize_message_id(payload["message_id"])
        resolve_read_scope(resolved_integrations)
    except FeishuReadError:
        return {}, "Feishu read input or current-chat authority is invalid"
    return {"message_id": message_id}, None
