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

import logging
import os

from pydantic import Field

from config.constants.feishu import (
    ALERTPUSH_APP_ID_ENV,
    ALERTPUSH_APP_SECRET_ENV,
    FEISHU_ALARM_RECEIVE_ID_ENV,
    FEISHU_ALARM_RECEIVE_ID_TYPE_ENV,
    FEISHU_ALLOWED_OPEN_IDS_ENV,
    FEISHU_APP_ID_ENV,
    FEISHU_APP_SECRET_ENV,
    FEISHU_CHAT_RECEIVE_ID_ENV,
    FEISHU_CHAT_RECEIVE_ID_TYPE_ENV,
)
from config.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)


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
    # The gateway transport reads the allowlist through this leaf, so it travels
    # with the chat credentials. The alert-push app has no inbound path.
    allowed_open_ids: str = ""


def _feishu_store_config() -> dict[str, object]:
    """Return the Feishu integration's effective config (store merged with env), or ``{}``.

    A store read or parse failure degrades to ``{}``; errors outside that known
    set are not swallowed, so an unexpected failure surfaces instead of silently
    falling back to stale environment credentials.

    The ``integrations.catalog`` import is function-local on purpose: that
    module is on the background-notification boot-path forbidden list, so a
    module-scope import would be pulled in by every importer of this leaf.
    """
    try:
        from integrations.catalog import resolve_effective_integrations

        entry = resolve_effective_integrations().get("feishu", {})
        config = entry.get("config", {}) if isinstance(entry, dict) else {}
        return config if isinstance(config, dict) else {}
    except (ImportError, KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
        logger.debug("Failed to resolve Feishu credentials from the store: %s", exc, exc_info=True)
        return {}


def _resolve_chat_receive_id_type(store_config: dict[str, object]) -> str:
    store_type = str(store_config.get("receive_id_type") or "").strip()
    if store_type:
        return store_type
    return os.environ.get(FEISHU_CHAT_RECEIVE_ID_TYPE_ENV, "").strip() or "chat_id"


def load_chat_credentials_from_env() -> FeishuChatCredentials:
    """Resolve chat-app credentials: store → env, secret → credentials file/keyring.

    The store tier is what guided setup writes to; an unconfigured store leaves
    a plain-``.env`` deployment reading exactly what it read before.
    """
    from config.llm_credentials import resolve_env_credential

    store_config = _feishu_store_config()
    return FeishuChatCredentials(
        app_id=str(store_config.get("app_id") or "").strip()
        or os.environ.get(FEISHU_APP_ID_ENV, ""),
        app_secret=str(store_config.get("app_secret") or "").strip()
        or resolve_env_credential(FEISHU_APP_SECRET_ENV),
        receive_id=str(store_config.get("receive_id") or "").strip()
        or os.environ.get(FEISHU_CHAT_RECEIVE_ID_ENV, ""),
        receive_id_type=_resolve_chat_receive_id_type(store_config),
        allowed_open_ids=str(store_config.get("allowed_open_ids") or "").strip()
        or os.environ.get(FEISHU_ALLOWED_OPEN_IDS_ENV, ""),
    )


def load_credentials_from_env(
    channel_override: str | None = None,
) -> FeishuAlarmCredentials:
    """Resolve alert-push credentials (``ALERTPUSH_*``) from the environment.

    The alert-push application is not a catalog integration — it is one-way and
    has no guided-setup entry — so this path deliberately has no store tier.
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
