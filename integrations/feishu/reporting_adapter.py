"""Feishu ``ReportDeliveryAdapter`` implementation.

Registers itself into the platform-level delivery registry at import time so
``tools.investigation.reporting.delivery.dispatch`` never imports
``integrations.feishu`` directly (same layering rule as the other vendor
adapters — T-4 layering audit, issue #3352).

Report delivery posts through the interactive app (``FEISHU_APP_ID``) rather
than the alert-push app, so a rendered report lands in a conversation the chat
bot already serves and the recipient can reply to it.
"""

from __future__ import annotations

import logging
from typing import Any

from infrastructure.delivery.reporting.delivery_registry import (
    DeliveryContext,
    register_delivery_adapter,
)

logger = logging.getLogger(__name__)


class _FeishuReportDeliveryAdapter:
    """Feishu delivery adapter — posts the report to the configured chat target."""

    name = "feishu"

    def deliver(
        self,
        state: DeliveryContext,  # noqa: ARG002
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],  # noqa: ARG002
    ) -> bool:
        # Imported lazily so the lark SDK does not join the report path's import
        # graph unless a delivery is actually attempted.
        from integrations.feishu.credentials import load_chat_credentials_from_env
        from integrations.feishu.document_delivery import deliver_feishu_document

        creds = load_chat_credentials_from_env()
        if not creds.app_id or not creds.app_secret or not creds.receive_id:
            logger.debug(
                "[publish] feishu delivery: chat app credentials or FEISHU_CHAT_RECEIVE_ID missing"
            )
            return False

        result = deliver_feishu_document(
            app_id=creds.app_id,
            app_secret=creds.app_secret,
            receive_id=creds.receive_id,
            receive_id_type=creds.receive_id_type,
            markdown=str(messages.get("markdown_text") or ""),
        )
        if result.status.value == "failed":
            logger.warning("[publish] Feishu document delivery failed")
        return result.attempted


feishu_delivery_adapter = _FeishuReportDeliveryAdapter()
register_delivery_adapter(feishu_delivery_adapter)

__all__ = ["feishu_delivery_adapter"]
