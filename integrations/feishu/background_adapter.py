"""Feishu delivery for background RCA completion notifications."""

from __future__ import annotations

import logging

from core.domain.background_investigations import BackgroundInvestigationRecord
from infrastructure.delivery.notifications.outbound_registry import BACKGROUND_RCA

logger = logging.getLogger(__name__)


def deliver_feishu_notification(record: BackgroundInvestigationRecord) -> str:
    """Send the background-RCA completion summary to Feishu; return a result string."""
    # Imported lazily: feishu delivery only fires on background-RCA completion,
    # so the lark SDK must not load into the base REPL boot import path.
    from infrastructure.delivery.notifications.rca_summary import format_background_rca_markdown
    from integrations.feishu.credentials import load_credentials_from_env
    from integrations.feishu.document_delivery import deliver_feishu_document

    creds = load_credentials_from_env()
    if not creds.app_id or not creds.app_secret or not creds.receive_id:
        return (
            "missing feishu integration: ALERTPUSH_APP_ID, ALERTPUSH_APP_SECRET, "
            "and FEISHU_ALARM_RECEIVE_ID must be set."
        )

    try:
        result = deliver_feishu_document(
            app_id=creds.app_id,
            app_secret=creds.app_secret,
            receive_id=creds.receive_id,
            receive_id_type=creds.receive_id_type,
            markdown=format_background_rca_markdown(record),
        )
    except Exception:  # noqa: BLE001
        logger.warning("Feishu background document delivery failed")
        return "failed: Feishu delivery failed"
    return "sent" if result.successful else "failed: Feishu delivery failed"


class _FeishuBackgroundAdapter:
    """Registry adapter wrapping :func:`deliver_feishu_notification`."""

    name = "feishu"
    capabilities = frozenset({BACKGROUND_RCA})

    def deliver(self, record: BackgroundInvestigationRecord) -> str:
        return deliver_feishu_notification(record)


feishu_background_adapter = _FeishuBackgroundAdapter()
