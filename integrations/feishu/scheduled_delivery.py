"""Scheduled-delivery adapter: post a scheduled task's message to Feishu."""

from __future__ import annotations

from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.scheduling.scheduler.credentials import resolve_feishu_credentials
from infrastructure.scheduling.scheduler.delivery import strip_html
from infrastructure.scheduling.scheduler.types import ScheduledTask
from infrastructure.text.truncation import truncate


class FeishuScheduledDelivery:
    """Deliver a scheduled task's message through the Feishu chat app.

    The task's own ``chat_id`` is the destination; ``FEISHU_CHAT_RECEIVE_ID``
    only backs tasks created without one. Feishu renders plain text, so mark-up
    from the shared message builder is stripped before it is sent.
    """

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        creds = resolve_feishu_credentials(dict(task.params))
        app_id = creds.get("app_id", "")
        app_secret = creds.get("app_secret", "")
        if not app_id or not app_secret:
            return False, "Missing app_id or app_secret for Feishu", ""

        receive_id = (task.chat_id or "").strip() or creds.get("receive_id", "")
        if not receive_id:
            return (
                False,
                "Missing chat_id for Feishu (pass --chat-id or set FEISHU_CHAT_RECEIVE_ID)",
                "",
            )

        # Imported lazily so the lark SDK stays out of the scheduler's boot path
        # — the same rule the background-RCA adapter follows.
        from integrations.feishu.delivery import post_feishu_message

        plain_message = truncate(strip_html(message), MAX_MESSAGE_SIZE, suffix="…")
        ok, error, message_id = post_feishu_message(
            app_id,
            app_secret,
            receive_id,
            creds.get("receive_id_type", "chat_id"),
            plain_message,
        )
        return (True, "", message_id) if ok else (False, error, "")


__all__ = ["FeishuScheduledDelivery"]
