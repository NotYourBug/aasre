"""Feishu delivery helper — posts a message via ``im.message.create``.

The one-way notification path (background-RCA completion notices and the
watchdog alarm) shares the raw transport here, mirroring
:mod:`integrations.rocketchat.delivery` where :mod:`integrations.feishu.alarms`
owns only the throttling + dispatch policy.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.delivery.notifications.redaction import redact_token
from infrastructure.text.truncation import truncate

logger = logging.getLogger(__name__)


def post_feishu_message(
    app_id: str,
    app_secret: str,
    receive_id: str,
    receive_id_type: str,
    text: str,
) -> tuple[bool, str, str]:
    """Send one text message via the pinned lark-oapi SDK.

    Returns ``(success, error, message_id)``. Never raises: a transport
    exception (bad credentials, network failure) becomes ``(False, error, "")``
    with the app secret redacted, matching the other vendors' transport helpers.
    """
    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
    request = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        .build()
    )
    try:
        response = client.im.v1.message.create(request)
    except Exception as exc:
        safe_error = redact_token(str(exc), app_secret)
        logger.warning("[feishu] post message exception: %s", safe_error)
        return False, safe_error, ""

    if response.success():
        data = getattr(response, "data", None)
        message_id = str(getattr(data, "message_id", "") or "") if data else ""
        return True, "", message_id

    error = redact_token(str(getattr(response, "msg", "") or ""), app_secret)
    logger.warning("[feishu] post message failed: %s", error)
    return False, error, ""


def send_feishu_report(report: str, feishu_ctx: dict[str, Any]) -> tuple[bool, str]:
    """Send a truncated report to Feishu. Returns ``(success, error)``."""
    app_id = str(feishu_ctx.get("app_id") or "").strip()
    app_secret = str(feishu_ctx.get("app_secret") or "").strip()
    receive_id = str(feishu_ctx.get("receive_id") or "").strip()
    receive_id_type = str(feishu_ctx.get("receive_id_type") or "chat_id").strip()
    if not app_id or not app_secret or not receive_id:
        return False, "Missing app_id, app_secret, or receive_id"
    text = truncate(report, MAX_MESSAGE_SIZE, suffix="…")
    ok, error, _message_id = post_feishu_message(
        app_id, app_secret, receive_id, receive_id_type, text
    )
    return (True, "") if ok else (False, error)


__all__ = ["post_feishu_message", "send_feishu_report"]
