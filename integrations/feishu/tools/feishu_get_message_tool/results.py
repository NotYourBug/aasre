"""Safe read results with one sanitized content representation."""

import json

from core.tool import ToolExecutionResult
from integrations.feishu.read_types import FeishuReadErrorCode, FeishuReadMessage

_ERRORS = {
    FeishuReadErrorCode.VALIDATION: "Feishu message ID is invalid",
    FeishuReadErrorCode.AUTHORIZATION: "Feishu read is unavailable in this current chat",
    FeishuReadErrorCode.CANCELLED: "Feishu read was cancelled",
    FeishuReadErrorCode.MESSAGE_UNAVAILABLE: "Feishu message is unavailable in this current chat",
    FeishuReadErrorCode.UNSUPPORTED_CONTENT: "Feishu message content is unsupported",
    FeishuReadErrorCode.CONTENT_TOO_LARGE: "Feishu message content exceeds the read limit",
    FeishuReadErrorCode.CONTENT_TOO_COMPLEX: "Feishu message content exceeds the complexity limit",
    FeishuReadErrorCode.RATE_LIMITED: "Feishu read was rate limited",
    FeishuReadErrorCode.UPSTREAM_ERROR: "Feishu read could not be completed",
}


def read_success(message: FeishuReadMessage) -> ToolExecutionResult:
    """Release only the sanitized bounded message content and verified identity."""
    payload = {
        "source": "feishu",
        "status": "read",
        "message_id": message.message_id,
        "chat_id": message.chat_id,
        "msg_type": message.msg_type,
        "body_content": message.content.body_content,
        "body_format": message.content.body_format,
        "truncated": message.content.truncated,
        "redacted": message.content.redacted,
        "resource_content_included": False,
        "content_trust": "untrusted",
    }
    return ToolExecutionResult(content=json.dumps(payload, ensure_ascii=False), details=payload)


def read_failure(code: FeishuReadErrorCode) -> ToolExecutionResult:
    """Return a stable failure without rejected identity or original content."""
    payload = {
        "source": "feishu",
        "status": "failed",
        "error_type": code.value,
        "error": _ERRORS[code],
    }
    return ToolExecutionResult(content=json.dumps(payload), details=payload, is_error=True)
