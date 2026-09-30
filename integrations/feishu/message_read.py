"""Guarded current-chat Feishu message reads."""

from collections.abc import Callable
from http import HTTPStatus

import lark_oapi as lark
from lark_oapi.api.im.v1 import GetMessageRequest, GetMessageResponse

from config.constants.feishu import (
    FEISHU_AUTHORIZATION_ERROR_CODES,
    FEISHU_MESSAGE_READ_TIMEOUT_SECONDS,
    FEISHU_MESSAGE_READ_UNAVAILABLE_ERROR_CODES,
    FEISHU_RATE_LIMIT_ERROR_CODES,
)
from integrations.feishu.message_content import normalize_read_content
from integrations.feishu.read_types import (
    FeishuReadError,
    FeishuReadErrorCode,
    FeishuReadMessage,
    FeishuReadScope,
)


def _response_error(response: GetMessageResponse) -> FeishuReadErrorCode | None:
    code = response.code
    if (
        code in FEISHU_RATE_LIMIT_ERROR_CODES
        or getattr(response.raw, "status_code", None) == HTTPStatus.TOO_MANY_REQUESTS
    ):
        return FeishuReadErrorCode.RATE_LIMITED
    if response.success():
        return None
    if (
        code in FEISHU_AUTHORIZATION_ERROR_CODES
        or code in FEISHU_MESSAGE_READ_UNAVAILABLE_ERROR_CODES
    ):
        return FeishuReadErrorCode.MESSAGE_UNAVAILABLE
    return FeishuReadErrorCode.UPSTREAM_ERROR


def _read_guarded(
    *,
    app_id: str,
    app_secret: str,
    message_id: str,
    scope: FeishuReadScope,
    cancel_requested: Callable[[], bool],
) -> FeishuReadMessage:
    if app_id != scope.app_id or not app_secret.strip():
        raise FeishuReadError(FeishuReadErrorCode.AUTHORIZATION)
    if cancel_requested():
        raise FeishuReadError(FeishuReadErrorCode.CANCELLED)
    client = (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .timeout(FEISHU_MESSAGE_READ_TIMEOUT_SECONDS)
        .build()
    )
    request = (
        GetMessageRequest.builder()
        .message_id(message_id)
        .user_id_type("open_id")
        .card_msg_content_type("user_card_content")
        .build()
    )
    response = client.im.v1.message.get(request)
    if cancel_requested():
        raise FeishuReadError(FeishuReadErrorCode.CANCELLED)
    error = _response_error(response)
    if error is not None:
        raise FeishuReadError(error)
    items = getattr(response.data, "items", None)
    if not isinstance(items, list) or len(items) != 1:
        raise FeishuReadError(FeishuReadErrorCode.MESSAGE_UNAVAILABLE)
    item = items[0]
    if (
        getattr(item, "message_id", None) != message_id
        or getattr(item, "chat_id", None) != scope.chat_id
        or getattr(item, "deleted", None) is not False
    ):
        raise FeishuReadError(FeishuReadErrorCode.MESSAGE_UNAVAILABLE)
    msg_type = getattr(item, "msg_type", None)
    if not isinstance(msg_type, str) or not msg_type or msg_type == "merge_forward":
        raise FeishuReadError(FeishuReadErrorCode.UNSUPPORTED_CONTENT)
    raw_content = getattr(getattr(item, "body", None), "content", None)
    if not isinstance(raw_content, str):
        raise FeishuReadError(FeishuReadErrorCode.UNSUPPORTED_CONTENT)
    content = normalize_read_content(raw_content)
    if cancel_requested():
        raise FeishuReadError(FeishuReadErrorCode.CANCELLED)
    return FeishuReadMessage(message_id, scope.chat_id, msg_type, content)


def read_current_message(
    *,
    app_id: str,
    app_secret: str,
    message_id: str,
    scope: FeishuReadScope,
    cancel_requested: Callable[[], bool],
) -> FeishuReadMessage:
    """Release one sanitized exact live message, or raise a code-only unchained error."""
    code = FeishuReadErrorCode.UPSTREAM_ERROR
    try:
        return _read_guarded(
            app_id=app_id,
            app_secret=app_secret,
            message_id=message_id,
            scope=scope,
            cancel_requested=cancel_requested,
        )
    except FeishuReadError as exc:
        code = exc.code
    except Exception:
        pass
    raise FeishuReadError(code)
