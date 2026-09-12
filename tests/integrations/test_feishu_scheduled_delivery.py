"""Tests for integrations.feishu.scheduled_delivery."""

from __future__ import annotations

import pytest

from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.feishu.scheduled_delivery import FeishuScheduledDelivery


def _task(*, chat_id: str = "", params: dict[str, str] | None = None) -> ScheduledTask:
    return ScheduledTask(
        kind=TaskKind.DAILY_SUMMARY,
        cron="0 9 * * *",
        provider=Provider.FEISHU,
        chat_id=chat_id,
        params=params or {},
    )


def _install_fake_credentials(monkeypatch: pytest.MonkeyPatch, creds: dict[str, str]) -> None:
    """Patch the credential seam so no env or credentials file is consulted."""

    def _fake_resolve(_task_params: dict[str, str]) -> dict[str, str]:
        return creds

    monkeypatch.setattr(
        "integrations.feishu.scheduled_delivery.resolve_feishu_credentials", _fake_resolve
    )


def _install_fake_post(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: tuple[bool, str, str] = (True, "", "om_1"),
) -> list[tuple[str, str, str, str, str]]:
    """Patch the transport seam and capture every call it receives."""
    calls: list[tuple[str, str, str, str, str]] = []

    def _fake_post(
        app_id: str,
        app_secret: str,
        receive_id: str,
        receive_id_type: str,
        text: str,
    ) -> tuple[bool, str, str]:
        calls.append((app_id, app_secret, receive_id, receive_id_type, text))
        return result

    monkeypatch.setattr("integrations.feishu.delivery.post_feishu_message", _fake_post)
    return calls


def _chat_creds(**overrides: str) -> dict[str, str]:
    creds = {
        "app_id": "cli_chat",
        "app_secret": "s_chat",
        "receive_id": "oc_default",
        "receive_id_type": "chat_id",
    }
    creds.update(overrides)
    return creds


def test_missing_credentials_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_credentials(monkeypatch, {})
    calls = _install_fake_post(monkeypatch)

    ok, error, _message_id = FeishuScheduledDelivery().deliver(_task(chat_id="oc_x"), "digest")

    assert ok is False
    assert "app_id" in error
    assert calls == []


def test_task_chat_id_wins_over_the_configured_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scheduled task carries an explicit --chat-id; it overrides the default."""
    _install_fake_credentials(monkeypatch, _chat_creds())
    calls = _install_fake_post(monkeypatch)

    ok, _error, message_id = FeishuScheduledDelivery().deliver(
        _task(chat_id="oc_explicit"), "digest"
    )

    assert ok is True
    assert message_id == "om_1"
    assert len(calls) == 1
    assert calls[0][:4] == ("cli_chat", "s_chat", "oc_explicit", "chat_id")


def test_falls_back_to_the_configured_receive_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tasks created without a chat_id fall back to FEISHU_CHAT_RECEIVE_ID."""
    _install_fake_credentials(monkeypatch, _chat_creds())
    calls = _install_fake_post(monkeypatch)

    ok, _error, _message_id = FeishuScheduledDelivery().deliver(_task(chat_id=""), "digest")

    assert ok is True
    assert calls[0][2] == "oc_default"


def test_fallback_keeps_its_own_receive_id_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fallback aimed at a person posts as that person."""
    _install_fake_credentials(
        monkeypatch, _chat_creds(receive_id="ou_fallback", receive_id_type="open_id")
    )
    calls = _install_fake_post(monkeypatch)

    ok, _error, _message_id = FeishuScheduledDelivery().deliver(_task(chat_id=""), "digest")

    assert ok is True
    assert calls[0][2:4] == ("ou_fallback", "open_id")


def test_task_chat_id_does_not_inherit_the_fallback_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The type travels with the destination that won, not with the fallback.

    A task's own ``--chat-id`` names a chat. Applying the configured fallback's
    type to it would send a chat id typed as an ``open_id`` whenever that
    fallback targets a person.
    """
    _install_fake_credentials(
        monkeypatch, _chat_creds(receive_id="ou_fallback", receive_id_type="open_id")
    )
    calls = _install_fake_post(monkeypatch)

    ok, _error, _message_id = FeishuScheduledDelivery().deliver(
        _task(chat_id="oc_explicit"), "digest"
    )

    assert ok is True
    assert calls[0][2:4] == ("oc_explicit", "chat_id")


def test_refuses_without_any_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_credentials(monkeypatch, _chat_creds(receive_id=""))
    calls = _install_fake_post(monkeypatch)

    ok, error, _message_id = FeishuScheduledDelivery().deliver(_task(chat_id=""), "digest")

    assert ok is False
    assert "chat_id" in error
    assert calls == []


def test_delivers_html_stripped_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feishu renders plain text, so mark-up from the shared builder is stripped."""
    _install_fake_credentials(monkeypatch, _chat_creds())
    calls = _install_fake_post(monkeypatch)

    ok, _error, _message_id = FeishuScheduledDelivery().deliver(
        _task(chat_id="oc_x"), "<b>Daily</b> summary"
    )

    assert ok is True
    assert calls[0][4] == "Daily summary"


def test_transport_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_credentials(monkeypatch, _chat_creds())
    _install_fake_post(monkeypatch, result=(False, "denied", ""))

    ok, error, message_id = FeishuScheduledDelivery().deliver(_task(chat_id="oc_x"), "digest")

    assert ok is False
    assert error == "denied"
    assert message_id == ""
