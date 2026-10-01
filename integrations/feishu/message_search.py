"""Guarded bounded current-chat history scans."""

from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import ListMessageRequest, ListMessageResponse
from lark_oapi.core.enum import AccessTokenType

from config.constants.feishu import (
    FEISHU_HISTORY_UNAVAILABLE_ERROR_CODES,
    FEISHU_MESSAGE_READ_MAX_ID_CHARS,
    FEISHU_MESSAGE_READ_MAX_INPUT_BYTES,
    FEISHU_MESSAGE_READ_TIMEOUT_SECONDS,
    FEISHU_RATE_LIMIT_ERROR_CODES,
    FEISHU_SEARCH_MAX_BODY_BYTES,
    FEISHU_SEARCH_MAX_MSG_TYPE_CHARS,
    FEISHU_SEARCH_MAX_PAGE_TOKEN_CHARS,
    FEISHU_SEARCH_MAX_PAGES,
    FEISHU_SEARCH_METADATA_TYPES,
    FEISHU_SEARCH_PAGE_SIZE,
    FEISHU_SEARCH_TEXT_TYPES,
)
from integrations.feishu.read_types import FeishuReadScope
from integrations.feishu.search_content import make_search_preview, normalize_search_content
from integrations.feishu.search_types import (
    FeishuSearchContent,
    FeishuSearchError,
    FeishuSearchErrorCode,
    FeishuSearchInput,
    FeishuSearchItem,
    FeishuSearchResult,
    FeishuSearchStopReason,
)


@dataclass(frozen=True)
class _VerifiedItem:
    source: Any
    message_id: str
    msg_type: str
    create_time_ms: int
    deleted: bool


@dataclass
class _Scan:
    items: list[FeishuSearchItem] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)
    body_bytes: int = 0
    scanned: int = 0
    matched: int = 0
    unsearchable: int = 0


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= FEISHU_MESSAGE_READ_MAX_ID_CHARS
        and value.isascii()
        and all(char.isalnum() or char in "_-" for char in value)
    )


def _utf8(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _check_cancel(cancel_requested: Callable[[], bool]) -> None:
    if cancel_requested():
        raise FeishuSearchError(FeishuSearchErrorCode.CANCELLED)


def _response_error(response: ListMessageResponse) -> FeishuSearchErrorCode | None:
    code = response.code
    status = getattr(response.raw, "status_code", None)
    if status == HTTPStatus.TOO_MANY_REQUESTS or code in FEISHU_RATE_LIMIT_ERROR_CODES:
        return FeishuSearchErrorCode.RATE_LIMITED
    if type(code) is not int:
        return FeishuSearchErrorCode.UPSTREAM_ERROR
    if code in FEISHU_HISTORY_UNAVAILABLE_ERROR_CODES:
        return FeishuSearchErrorCode.HISTORY_UNAVAILABLE
    if code != 0 or status not in (None, HTTPStatus.OK):
        return FeishuSearchErrorCode.UPSTREAM_ERROR
    return None


def _creation_time(value: Any, inputs: FeishuSearchInput) -> int:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        # Leading zeroes do not increase integer work; the captured end bounds digits.
        digits = value.lstrip("0") or "0"
        if len(digits) <= len(str(inputs.end_time * 1000)):
            value = int(digits)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not inputs.start_time * 1000 <= value <= inputs.end_time * 1000
    ):
        raise FeishuSearchError(FeishuSearchErrorCode.HISTORY_UNAVAILABLE)
    return value


def _page(
    response: ListMessageResponse,
    scope: FeishuReadScope,
    inputs: FeishuSearchInput,
    tokens: set[str],
) -> tuple[list[_VerifiedItem], bool, str | None]:
    error = _response_error(response)
    if error is not None:
        raise FeishuSearchError(error)
    items = getattr(response.data, "items", None)
    has_more = getattr(response.data, "has_more", None)
    token = getattr(response.data, "page_token", None)
    if (
        not isinstance(items, list)
        or len(items) > FEISHU_SEARCH_PAGE_SIZE
        or type(has_more) is not bool
    ):
        raise FeishuSearchError(FeishuSearchErrorCode.UPSTREAM_ERROR)
    if has_more:
        if (
            not isinstance(token, str)
            or not token
            or len(token) > FEISHU_SEARCH_MAX_PAGE_TOKEN_CHARS
            or not _utf8(token)
            or token in tokens
        ):
            raise FeishuSearchError(FeishuSearchErrorCode.UPSTREAM_ERROR)
        tokens.add(token)
    elif token not in (None, ""):
        raise FeishuSearchError(FeishuSearchErrorCode.UPSTREAM_ERROR)
    verified: list[_VerifiedItem] = []
    for item in items:
        message_id = getattr(item, "message_id", None)
        msg_type = getattr(item, "msg_type", None)
        deleted = getattr(item, "deleted", None)
        if (
            getattr(item, "chat_id", None) != scope.chat_id
            or not _identifier(message_id)
            or type(deleted) is not bool
            or not isinstance(msg_type, str)
            or not 0 < len(msg_type) <= FEISHU_SEARCH_MAX_MSG_TYPE_CHARS
            or not _utf8(msg_type)
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in msg_type)
        ):
            raise FeishuSearchError(FeishuSearchErrorCode.HISTORY_UNAVAILABLE)
        created = _creation_time(getattr(item, "create_time", None), inputs)
        verified.append(_VerifiedItem(item, message_id, msg_type, created, deleted))
    return verified, has_more, token


def _body_size(raw: Any) -> int | None:
    if not isinstance(raw, str) or len(raw) > FEISHU_MESSAGE_READ_MAX_INPUT_BYTES:
        return None
    size: int | None = None
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeError:
        return None
    return size if size <= FEISHU_MESSAGE_READ_MAX_INPUT_BYTES else None


def _scan_item(item: _VerifiedItem, inputs: FeishuSearchInput, scan: _Scan) -> bool:
    scan.scanned += 1
    if item.message_id in scan.seen_ids:
        return True
    scan.seen_ids.add(item.message_id)
    if item.deleted:
        return True
    content = FeishuSearchContent(None, False)
    if item.msg_type in FEISHU_SEARCH_TEXT_TYPES:
        raw = getattr(getattr(item.source, "body", None), "content", None)
        size = _body_size(raw)
        if size is not None:
            if scan.body_bytes + size > FEISHU_SEARCH_MAX_BODY_BYTES:
                return False
            scan.body_bytes += size
            content = normalize_search_content(raw)
    elif item.msg_type not in FEISHU_SEARCH_METADATA_TYPES:
        scan.unsearchable += 1
        return True
    if content.text is None:
        scan.unsearchable += 1
    preview = make_search_preview(content, inputs.query)
    if inputs.query and preview is None:
        return True
    scan.matched += 1
    if len(scan.items) < inputs.limit:
        scan.items.append(
            FeishuSearchItem(
                item.message_id,
                item.msg_type,
                item.create_time_ms,
                preview[0] if preview is not None else "",
                preview is not None,
                preview[1] if preview is not None else False,
                content.redacted,
            )
        )
    return True


def _search_guarded(
    *,
    app_id: str,
    app_secret: str,
    scope: FeishuReadScope,
    inputs: FeishuSearchInput,
    cancel_requested: Callable[[], bool],
) -> FeishuSearchResult:
    if (
        app_id != scope.app_id
        or not _identifier(scope.app_id)
        or not _identifier(scope.chat_id)
        or not isinstance(app_secret, str)
        or not app_secret.strip()
    ):
        raise FeishuSearchError(FeishuSearchErrorCode.AUTHORIZATION)
    _check_cancel(cancel_requested)
    client = (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .timeout(FEISHU_MESSAGE_READ_TIMEOUT_SECONDS)
        .build()
    )
    scan = _Scan()
    tokens: set[str] = set()
    token: str | None = None
    stop = FeishuSearchStopReason.PAGE_LIMIT
    pages = 0
    for _ in range(FEISHU_SEARCH_MAX_PAGES):
        _check_cancel(cancel_requested)
        builder = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(scope.chat_id)
            .start_time(str(inputs.start_time))
            .end_time(str(inputs.end_time))
            .sort_type("ByCreateTimeDesc")
            .page_size(FEISHU_SEARCH_PAGE_SIZE)
            .card_msg_content_type("user_card_content")
        )
        if token is not None:
            builder.page_token(token)
        request = builder.build()
        request.token_types = {AccessTokenType.TENANT}
        response = client.im.v1.message.list(request)
        pages += 1
        _check_cancel(cancel_requested)
        items, has_more, token = _page(response, scope, inputs, tokens)
        stopped = False
        for item in items:
            _check_cancel(cancel_requested)
            if not _scan_item(item, inputs, scan):
                stopped = True
                break
        if stopped:
            stop = FeishuSearchStopReason.WORK_LIMIT
            break
        if not has_more:
            stop = FeishuSearchStopReason.SOURCE_EXHAUSTED
            break
    _check_cancel(cancel_requested)
    return FeishuSearchResult(
        scope.chat_id,
        inputs.start_time,
        inputs.end_time,
        tuple(scan.items),
        pages,
        scan.scanned,
        scan.matched,
        scan.unsearchable,
        stop == FeishuSearchStopReason.SOURCE_EXHAUSTED,
        stop,
    )


def search_current_messages(
    *,
    app_id: str,
    app_secret: str,
    scope: FeishuReadScope,
    inputs: FeishuSearchInput,
    cancel_requested: Callable[[], bool],
) -> FeishuSearchResult:
    """Release bounded sanitized search results, or a code-only unchained error."""
    code = FeishuSearchErrorCode.UPSTREAM_ERROR
    try:
        return _search_guarded(
            app_id=app_id,
            app_secret=app_secret,
            scope=scope,
            inputs=inputs,
            cancel_requested=cancel_requested,
        )
    except FeishuSearchError as exc:
        code = exc.code
    except Exception:
        pass
    raise FeishuSearchError(code)
