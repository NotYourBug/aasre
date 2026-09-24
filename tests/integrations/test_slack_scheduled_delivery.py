from __future__ import annotations

from types import SimpleNamespace

import pytest

from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.slack.scheduled_delivery import SlackScheduledDelivery


def _task(*, chat_id: str = "C123") -> ScheduledTask:
    return ScheduledTask(
        id="scheduled-slack",
        kind=TaskKind.DAILY_SUMMARY,
        cron="0 9 * * *",
        provider=Provider.SLACK,
        chat_id=chat_id,
    )


def test_token_delivery_preserves_channel_and_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def _post_json(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            ok=True,
            status_code=200,
            data={"ok": True, "ts": "171.2"},
            text="",
            error="",
        )

    monkeypatch.setattr(
        "integrations.slack.scheduled_delivery.resolve_slack_credentials",
        lambda _params: {"access_token": "xoxb-test"},
    )
    monkeypatch.setattr("integrations.slack.scheduled_delivery.post_json", _post_json)

    result = SlackScheduledDelivery().deliver(_task(), "# Scheduled\n**report**")

    assert result == (True, "", "171.2")
    assert calls[0]["payload"] == {"channel": "C123", "text": "*Scheduled*\n*report*"}


def test_webhook_delivery_keeps_webhook_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def _send(message: str, *, webhook_url: str) -> tuple[bool, str]:
        calls.append((message, webhook_url))
        return True, ""

    monkeypatch.setattr(
        "integrations.slack.scheduled_delivery.resolve_slack_credentials",
        lambda _params: {"webhook_url": "https://hooks.slack.test/T/B/x"},
    )
    monkeypatch.setattr(
        "integrations.slack.scheduled_delivery.send_slack_webhook_message", _send
    )

    result = SlackScheduledDelivery().deliver(
        _task(chat_id=""),
        "**Webhook** report",
    )

    assert result == (True, "", "")
    assert calls == [("*Webhook* report", "https://hooks.slack.test/T/B/x")]
