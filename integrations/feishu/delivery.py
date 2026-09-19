"""Feishu delivery helper — posts a message via ``im.message.create``.

The one-way notification path (background-RCA completion notices and the
watchdog alarm) shares the raw transport here, mirroring
:mod:`integrations.rocketchat.delivery` where :mod:`integrations.feishu.alarms`
owns only the throttling + dispatch policy.
"""

from __future__ import annotations

import json
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.text.truncation import truncate
from integrations.feishu.delivery_types import (
    FeishuDeliveryErrorCategory,
    FeishuMessageSendResult,
    FeishuSendCertainty,
    classify_feishu_rejection,
)


def post_feishu_message(
    app_id: str,
    app_secret: str,
    receive_id: str,
    receive_id_type: str,
    text: str,
) -> FeishuMessageSendResult:
    """Send one text message via the pinned lark-oapi SDK.

    The result contains only stable fields. Client/request construction and the
    visible transport call have separate certainty boundaries.
    """
    try:
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
    except Exception:
        return FeishuMessageSendResult(
            accepted=False,
            message_id="",
            error_category=FeishuDeliveryErrorCategory.TRANSPORT,
            certainty=FeishuSendCertainty.DEFINITELY_NOT_SENT,
        )

    try:
        response = client.im.v1.message.create(request)
    except Exception:
        return FeishuMessageSendResult(
            accepted=False,
            message_id="",
            error_category=FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN,
            certainty=FeishuSendCertainty.MAYBE_SENT,
        )

    try:
        accepted = bool(response.success())
        code = int(getattr(response, "code", 0) or 0)
        data = getattr(response, "data", None)
        message_id = str(getattr(data, "message_id", "") or "") if data else ""
    except Exception:
        return FeishuMessageSendResult(
            accepted=False,
            message_id="",
            error_category=FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN,
            certainty=FeishuSendCertainty.MAYBE_SENT,
        )

    if accepted and message_id:
        return FeishuMessageSendResult(
            accepted=True,
            message_id=message_id,
            error_category=None,
            certainty=FeishuSendCertainty.CONFIRMED_SENT,
        )
    if accepted:
        return FeishuMessageSendResult(
            accepted=False,
            message_id="",
            error_category=FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN,
            certainty=FeishuSendCertainty.MAYBE_SENT,
        )
    return FeishuMessageSendResult(
        accepted=False,
        message_id="",
        error_category=classify_feishu_rejection(code),
        certainty=FeishuSendCertainty.DEFINITELY_NOT_SENT,
    )


def send_feishu_report(report: str, feishu_ctx: dict[str, Any]) -> tuple[bool, str]:
    """Send a truncated report to Feishu. Returns ``(success, error)``."""
    app_id = str(feishu_ctx.get("app_id") or "").strip()
    app_secret = str(feishu_ctx.get("app_secret") or "").strip()
    receive_id = str(feishu_ctx.get("receive_id") or "").strip()
    receive_id_type = str(feishu_ctx.get("receive_id_type") or "chat_id").strip()
    if not app_id or not app_secret or not receive_id:
        return False, "Missing app_id, app_secret, or receive_id"
    text = truncate(report, MAX_MESSAGE_SIZE, suffix="…")
    result = post_feishu_message(app_id, app_secret, receive_id, receive_id_type, text)
    return (True, "") if result.accepted else (False, "Feishu delivery failed")


__all__ = ["post_feishu_message", "send_feishu_report"]
