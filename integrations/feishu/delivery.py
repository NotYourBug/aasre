"""Feishu delivery helper —posts a message via ``im.message.create``.

The one-way notification path (background-RCA completion notices and the
watchdog alarm) shares the raw transport here, mirroring
:mod:`integrations.rocketchat.delivery` where :mod:`integrations.feishu.alarms`
owns only the throttling + dispatch policy.
"""

from __future__ import annotations

import json

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

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


__all__ = ["post_feishu_message"]
