"""Feishu credential resolution for both apps.

The alert-push app (``ALERTPUSH_*``) delivers one-way notifications (watchdog
alarms and background-RCA completion notices). The interactive app
(``FEISHU_*``) serves the chat transport and the deliveries that belong in a
conversation — investigation reports and scheduled tasks — targeting
``FEISHU_CHAT_RECEIVE_ID``.

This leaf holds only credential resolution so :mod:`integrations.feishu` stays
import-light — no lark SDK — mirroring :mod:`integrations.rocketchat.credentials`.
"""

from __future__ import annotations

import os

from pydantic import Field

from config.constants.feishu import (
    ALERTPUSH_APP_ID_ENV,
    ALERTPUSH_APP_SECRET_ENV,
    FEISHU_ALARM_RECEIVE_ID_ENV,
    FEISHU_ALARM_RECEIVE_ID_TYPE_ENV,
    FEISHU_APP_ID_ENV,
    FEISHU_APP_SECRET_ENV,
    FEISHU_CHAT_RECEIVE_ID_ENV,
    FEISHU_CHAT_RECEIVE_ID_TYPE_ENV,
)
from config.strict_config import StrictConfigModel


class FeishuAlarmCredentials(StrictConfigModel):
    app_id: str
    app_secret: str = Field(repr=False)
    receive_id: str
    receive_id_type: str = "chat_id"


class FeishuChatCredentials(StrictConfigModel):
    app_id: str
    app_secret: str = Field(repr=False)
    receive_id: str
    receive_id_type: str = "chat_id"


def load_chat_credentials_from_env() -> FeishuChatCredentials:
    """Read the interactive app's credentials and its default target.

    Both report delivery and scheduled delivery post through the chat app, so a
    delivered message lands in a conversation the chat bot already serves and
    the recipient can reply to it. ``FEISHU_CHAT_RECEIVE_ID`` names that
    destination; a caller with its own destination (a scheduled task's
    ``chat_id``) overrides it rather than relying on this default.
    """
    from config.llm_credentials import resolve_env_credential

    return FeishuChatCredentials(
        app_id=os.environ.get(FEISHU_APP_ID_ENV, ""),
        app_secret=resolve_env_credential(FEISHU_APP_SECRET_ENV),
        receive_id=os.environ.get(FEISHU_CHAT_RECEIVE_ID_ENV, ""),
        receive_id_type=os.environ.get(FEISHU_CHAT_RECEIVE_ID_TYPE_ENV, "chat_id"),
    )


def load_credentials_from_env(
    channel_override: str | None = None,
) -> FeishuAlarmCredentials:
    """Read ALERTPUSH_* + receive-id env vars into credentials.

    The app secret resolves env-first then the local credentials file (the
    same tier order as the other vendor credential leaves), so a secret saved
    via guided setup works without being exported.
    """
    from config.llm_credentials import resolve_env_credential

    return FeishuAlarmCredentials(
        app_id=os.environ.get(ALERTPUSH_APP_ID_ENV, ""),
        app_secret=resolve_env_credential(ALERTPUSH_APP_SECRET_ENV),
        receive_id=channel_override or os.environ.get(FEISHU_ALARM_RECEIVE_ID_ENV, ""),
        receive_id_type=os.environ.get(FEISHU_ALARM_RECEIVE_ID_TYPE_ENV, "chat_id"),
    )


__all__ = [
    "FeishuAlarmCredentials",
    "FeishuChatCredentials",
    "load_chat_credentials_from_env",
    "load_credentials_from_env",
]
