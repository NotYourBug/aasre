"""Classify Feishu store/env credentials into a runtime integration config."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import FeishuConfig

logger = logging.getLogger(__name__)


def validate_chat_config(credentials: dict[str, Any]) -> FeishuConfig | None:
    """Validate chat-app credentials without reporting failures or causing side effects."""
    app_id = str(credentials.get("app_id") or "").strip()
    if not app_id:
        return None
    return FeishuConfig.model_validate(
        {
            "app_id": app_id,
            "app_secret": str(credentials.get("app_secret") or "").strip(),
            "receive_id": str(credentials.get("receive_id") or "").strip(),
            "receive_id_type": str(credentials.get("receive_id_type") or "").strip() or "chat_id",
            "allowed_open_ids": str(credentials.get("allowed_open_ids") or "").strip(),
            "allowed_outbound_targets": str(
                credentials.get("allowed_outbound_targets") or ""
            ).strip(),
        }
    )


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Accept chat-app credentials (``app_id`` + ``app_secret``).

    Returns the portable config dict the catalog stores and the service key,
    or ``(None, None)`` when the record carries no chat-app app id. A record
    holding only alert-push credentials is not a chat integration.
    """
    try:
        config = validate_chat_config(credentials)
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="feishu", record_id=record_id)
        return None, None

    return (config.model_dump(exclude_none=True), "feishu") if config is not None else (None, None)


__all__ = ["classify", "validate_chat_config"]
