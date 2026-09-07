"""Feishu delivery for background RCA completion notifications."""

from __future__ import annotations

from core.domain.background_investigations import BackgroundInvestigationRecord
from infrastructure.delivery.notifications.outbound_registry import BACKGROUND_RCA


def deliver_feishu_notification(record: BackgroundInvestigationRecord) -> str:
    """Send the background-RCA completion summary to Feishu; return a result string."""
    # Imported lazily: feishu delivery only fires on background-RCA completion,
    # so the lark SDK must not load into the base REPL boot import path.
    from infrastructure.delivery.notifications.rca_summary import summary_sections
    from infrastructure.delivery.notifications.redaction import redact_token
    from integrations.feishu.credentials import load_credentials_from_env
    from integrations.feishu.delivery import send_feishu_report
    from integrations.smtp.delivery import format_background_rca_email

    creds = load_credentials_from_env()
    if not creds.app_id or not creds.app_secret or not creds.receive_id:
        return (
            "missing feishu integration: ALERTPUSH_APP_ID, ALERTPUSH_APP_SECRET, "
            "and FEISHU_ALARM_RECEIVE_ID must be set."
        )

    command, root_cause, top_analysis, next_steps = summary_sections(record)
    _subject, body = format_background_rca_email(
        task_id=record.task_id,
        command=command,
        root_cause=root_cause,
        top_analysis=top_analysis,
        next_steps=next_steps,
        stats=record.stats,
    )
    ok, error = send_feishu_report(
        body,
        {
            "app_id": creds.app_id,
            "app_secret": creds.app_secret,
            "receive_id": creds.receive_id,
            "receive_id_type": creds.receive_id_type,
        },
    )
    # The transport already redacts the app secret, but redact again at this
    # boundary: the string lands on the record and `/background show` renders
    # it, so a future unredacted transport branch must not leak here.
    return "sent" if ok else f"failed: {redact_token(error, creds.app_secret)}"


class _FeishuBackgroundAdapter:
    """Registry adapter wrapping :func:`deliver_feishu_notification`."""

    name = "feishu"
    capabilities = frozenset({BACKGROUND_RCA})

    def deliver(self, record: BackgroundInvestigationRecord) -> str:
        return deliver_feishu_notification(record)


feishu_background_adapter = _FeishuBackgroundAdapter()
