from __future__ import annotations

from typing import Any

import pytest

from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.discord.scheduled_delivery import DiscordScheduledDelivery


def test_delivery_preserves_embed_helper_context_and_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def _send(message: str, context: dict[str, Any]) -> tuple[bool, str]:
        calls.append((message, context))
        return True, ""

    monkeypatch.setattr(
        "integrations.discord.scheduled_delivery.resolve_discord_credentials",
        lambda _params: {"bot_token": "discord-token"},
    )
    monkeypatch.setattr("integrations.discord.scheduled_delivery.send_discord_report", _send)
    task = ScheduledTask(
        id="scheduled-discord",
        kind=TaskKind.DAILY_SUMMARY,
        cron="0 9 * * *",
        provider=Provider.DISCORD,
        chat_id="channel-7",
    )

    result = DiscordScheduledDelivery().deliver(task, "**Scheduled** report")

    assert result == (True, "", "")
    assert calls == [
        (
            "**Scheduled** report",
            {"channel_id": "channel-7", "bot_token": "discord-token"},
        )
    ]
