from __future__ import annotations

import pytest

from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.rocketchat.scheduled_delivery import RocketChatScheduledDelivery


def test_delivery_preserves_room_and_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str, str, str]] = []

    def _post(
        server_url: str,
        room_id: str,
        message: str,
        auth_token: str,
        user_id: str,
    ) -> tuple[bool, str, str]:
        calls.append((server_url, room_id, message, auth_token, user_id))
        return True, "", "msg-42"

    monkeypatch.setattr(
        "integrations.rocketchat.scheduled_delivery.resolve_rocketchat_credentials",
        lambda _params: {
            "server_url": "https://chat.example.test",
            "auth_token": "token",
            "user_id": "user",
        },
    )
    monkeypatch.setattr(
        "integrations.rocketchat.scheduled_delivery.post_rocketchat_message", _post
    )
    task = ScheduledTask(
        id="scheduled-rc",
        kind=TaskKind.DAILY_SUMMARY,
        cron="0 9 * * *",
        provider=Provider.ROCKETCHAT,
        chat_id="#ops",
    )

    result = RocketChatScheduledDelivery().deliver(task, "**Scheduled** report")

    assert result == (True, "", "msg-42")
    assert calls == [
        ("https://chat.example.test", "#ops", "**Scheduled** report", "token", "user")
    ]
