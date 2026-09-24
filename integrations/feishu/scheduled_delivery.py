"""Scheduled-delivery adapter for complete Feishu Markdown documents."""

from __future__ import annotations

from infrastructure.scheduling.scheduler.credentials import resolve_feishu_credentials
from infrastructure.scheduling.scheduler.types import ScheduledTask


class FeishuScheduledDelivery:
    """Deliver a scheduled task's canonical Markdown through the Feishu chat app.

    The task's own ``chat_id`` is the destination; ``FEISHU_CHAT_RECEIVE_ID``
    only backs tasks created without one. Destination IDs and types are treated
    as atomic pairs so a task override cannot inherit a configured identity type.
    """

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        creds = resolve_feishu_credentials(dict(task.params))
        app_id = creds.get("app_id", "").strip()
        app_secret = creds.get("app_secret", "").strip()
        if not app_id or not app_secret:
            return False, "Missing app_id or app_secret for Feishu", ""

        explicit_chat_id = (task.chat_id or "").strip()
        if explicit_chat_id:
            receive_id, receive_id_type = explicit_chat_id, "chat_id"
        else:
            receive_id = creds.get("receive_id", "").strip()
            receive_id_type = creds.get("receive_id_type", "").strip()

        if not receive_id or not receive_id_type:
            return False, "Missing Feishu delivery target", ""

        # Imported lazily so the lark SDK stays out of the scheduler's boot path.
        from integrations.feishu.document_delivery import deliver_feishu_document

        result = deliver_feishu_document(
            app_id=app_id,
            app_secret=app_secret,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            markdown=message,
        )
        if result.successful:
            return True, "", result.first_message_id
        return False, "Feishu delivery failed", ""


__all__ = ["FeishuScheduledDelivery"]
