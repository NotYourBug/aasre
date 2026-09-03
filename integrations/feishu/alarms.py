"""Feishu watchdog alarm dispatcher via im.message.create.

Credential resolution and raw transport live here alongside the dispatcher;
this module owns throttling + dispatch policy (mirrors
:mod:`integrations.rocketchat.alarms`). Env-var names come from
:mod:`config.constants.feishu`.
"""

from __future__ import annotations

import json
import logging
import os
import time

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    CreateMessageResponse,
)

from config.constants.feishu import (
    ALERTPUSH_APP_ID_ENV,
    ALERTPUSH_APP_SECRET_ENV,
    FEISHU_ALARM_RECEIVE_ID_ENV,
    FEISHU_ALARM_RECEIVE_ID_TYPE_ENV,
)
from config.strict_config import StrictConfigModel
from infrastructure.delivery.notifications.cooldown import CooldownGate
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.text.truncation import truncate

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 300.0


class FeishuAlarmCredentials(StrictConfigModel):
    app_id: str
    app_secret: str
    receive_id: str
    receive_id_type: str = "chat_id"


def load_credentials_from_env(
    channel_override: str | None = None,
) -> FeishuAlarmCredentials:
    """Read ALERTPUSH_* + receive-id env vars into credentials."""
    return FeishuAlarmCredentials(
        app_id=os.environ.get(ALERTPUSH_APP_ID_ENV, ""),
        app_secret=os.environ.get(ALERTPUSH_APP_SECRET_ENV, ""),
        receive_id=channel_override or os.environ.get(FEISHU_ALARM_RECEIVE_ID_ENV, ""),
        receive_id_type=os.environ.get(FEISHU_ALARM_RECEIVE_ID_TYPE_ENV, "chat_id"),
    )


def _create_message(creds: FeishuAlarmCredentials, text: str) -> CreateMessageResponse:
    """Send one text message via the pinned lark-oapi SDK."""
    client = lark.Client.builder().app_id(creds.app_id).app_secret(creds.app_secret).build()
    request = (
        CreateMessageRequest.builder()
        .receive_id_type(creds.receive_id_type)
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(creds.receive_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        .build()
    )
    return client.im.v1.message.create(request)


class FeishuAlarmDispatcher:
    """Deliver watchdog threshold alarms to a Feishu chat."""

    def __init__(
        self,
        creds: FeishuAlarmCredentials,
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._creds = creds
        self._gate = CooldownGate(cooldown_seconds)

    def dispatch(self, threshold_name: str, message: str) -> bool:
        """Send to Feishu unless this threshold is in cooldown."""
        now = self._now()

        remaining = self._gate.try_reserve(threshold_name, now)
        if remaining is not None:
            logger.debug(
                "alarm suppressed by cooldown: name=%s remaining=%.1fs",
                threshold_name,
                remaining,
            )
            return False

        text = truncate(f"[{threshold_name}] {message}", MAX_MESSAGE_SIZE, suffix="…")

        # The cooldown slot was reserved before this network call. If the
        # delivery raises or the SDK reports a non-zero code, the slot stays
        # armed for the cooldown window and the next caller for the same key
        # is silently suppressed — emit the same warning in both paths so
        # operators see the original failure instead of only the suppression
        # debug line.
        try:
            resp = _create_message(self._creds, text)
            ok = bool(resp.success())
        except Exception as exc:
            logger.warning(
                "alarm delivery raised and cooldown remains armed: name=%s error=%s",
                threshold_name,
                exc,
                exc_info=True,
            )
            return False

        if ok:
            return True

        logger.warning(
            "alarm delivery failed and cooldown remains armed: name=%s error=%s",
            threshold_name,
            getattr(resp, "msg", ""),
        )
        return False

    @staticmethod
    def _now() -> float:
        return time.monotonic()
