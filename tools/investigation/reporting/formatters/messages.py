"""Build all channel-specific report messages from a report context."""

from __future__ import annotations

from dataclasses import dataclass

from tools.investigation.reporting.context import ReportContext
from tools.investigation.reporting.formatters.report import (
    build_slack_blocks,
    format_markdown_message,
    format_slack_message,
    format_telegram_message,
)


@dataclass(frozen=True)
class ReportMessages:
    """Rendered report bodies for every publish channel."""

    markdown_text: str
    slack_text: str
    telegram_html: str
    slack_blocks: list[dict]


def build_report_messages(ctx: ReportContext) -> ReportMessages:
    """Render all report channel bodies from a shared context."""
    return ReportMessages(
        markdown_text=format_markdown_message(ctx),
        slack_text=format_slack_message(ctx),
        telegram_html=format_telegram_message(ctx),
        slack_blocks=build_slack_blocks(ctx),
    )
