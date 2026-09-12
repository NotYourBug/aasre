"""Tests for integrations.feishu.reporting_adapter."""

from __future__ import annotations

from typing import Any

import pytest

import integrations.feishu.reporting_adapter  # noqa: F401  (import registers the adapter)
from infrastructure.delivery.reporting.delivery_registry import (
    get_delivery_adapter,
    registered_delivery_adapter_names,
)


def _chat_creds(
    *,
    app_id: str = "cli_chat",
    app_secret: str = "s_chat",
    receive_id: str = "oc_target",
    receive_id_type: str = "chat_id",
) -> Any:
    return type(
        "C",
        (),
        {
            "app_id": app_id,
            "app_secret": app_secret,
            "receive_id": receive_id,
            "receive_id_type": receive_id_type,
        },
    )()


def _install_fake_chat_creds(monkeypatch: pytest.MonkeyPatch, creds: Any) -> None:
    """Patch the credential seam so no env or credentials file is consulted."""

    def _fake_load() -> Any:
        return creds

    monkeypatch.setattr(
        "integrations.feishu.credentials.load_chat_credentials_from_env", _fake_load
    )


def _install_fake_send(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: tuple[bool, str] = (True, ""),
) -> list[tuple[str, dict[str, Any]]]:
    """Patch the transport seam and capture every call it receives."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def _fake_send(report: str, ctx: dict[str, Any]) -> tuple[bool, str]:
        calls.append((report, ctx))
        return result

    monkeypatch.setattr("integrations.feishu.delivery.send_feishu_report", _fake_send)
    return calls


def _deliver(messages: dict[str, Any]) -> bool:
    adapter = get_delivery_adapter("feishu")
    assert adapter is not None
    return adapter.deliver({}, messages=messages, blocks=[])


def test_registers_under_the_feishu_name() -> None:
    assert "feishu" in registered_delivery_adapter_names()


def test_missing_credentials_skips_without_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_chat_creds(monkeypatch, _chat_creds(app_id="", app_secret="", receive_id=""))
    calls = _install_fake_send(monkeypatch)

    assert _deliver({"slack_text": "report"}) is False
    assert calls == []


def test_delivers_slack_text_through_the_chat_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feishu is a plain-text channel, so it reuses the ``slack_text`` rendering."""
    _install_fake_chat_creds(monkeypatch, _chat_creds())
    calls = _install_fake_send(monkeypatch)

    assert _deliver({"slack_text": "the report", "telegram_html": "<b>the report</b>"}) is True
    assert len(calls) == 1
    body, ctx = calls[0]
    assert body == "the report"
    assert ctx == {
        "app_id": "cli_chat",
        "app_secret": "s_chat",
        "receive_id": "oc_target",
        "receive_id_type": "chat_id",
    }


def test_failed_send_still_counts_as_attempted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry treats ``True`` as "attempted"; failures are logged, not skipped."""
    _install_fake_chat_creds(monkeypatch, _chat_creds())
    _install_fake_send(monkeypatch, result=(False, "denied"))

    assert _deliver({"slack_text": "report"}) is True
