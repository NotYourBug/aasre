"""Feishu alert-push credential resolution.

The alert-push app (``ALERTPUSH_*``) delivers one-way notifications (watchdog
alarms and background-RCA completion notices). This leaf holds only credential
resolution so :mod:`integrations.feishu` stays import-light — no lark SDK —
mirroring :mod:`integrations.rocketchat.credentials`.
"""

from __future__ import annotations

import os

from pydantic import Field

from config.constants.feishu import (
    ALERTPUSH_APP_ID_ENV,
    ALERTPUSH_APP_SECRET_ENV,
    FEISHU_ALARM_RECEIVE_ID_ENV,
    FEISHU_ALARM_RECEIVE_ID_TYPE_ENV,
)
from config.strict_config import StrictConfigModel


class FeishuAlarmCredentials(StrictConfigModel):
    app_id: str
    app_secret: str = Field(repr=False)
    receive_id: str
    receive_id_type: str = "chat_id"


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


__all__ = ["FeishuAlarmCredentials", "load_credentials_from_env"]
