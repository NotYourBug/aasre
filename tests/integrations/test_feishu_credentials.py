"""Tests for integrations.feishu.credentials."""

from __future__ import annotations

import pytest

from integrations.feishu.credentials import FeishuAlarmCredentials, load_credentials_from_env


def test_load_credentials_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERTPUSH_APP_ID", "cli_x")
    monkeypatch.setenv("ALERTPUSH_APP_SECRET", "s_x")
    monkeypatch.setenv("FEISHU_ALARM_RECEIVE_ID", "oc_x")
    monkeypatch.setenv("FEISHU_ALARM_RECEIVE_ID_TYPE", "open_id")

    creds = load_credentials_from_env()

    assert creds == FeishuAlarmCredentials(
        app_id="cli_x", app_secret="s_x", receive_id="oc_x", receive_id_type="open_id"
    )


def test_load_credentials_returns_empty_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ALERTPUSH_APP_ID",
        "ALERTPUSH_APP_SECRET",
        "FEISHU_ALARM_RECEIVE_ID",
        "FEISHU_ALARM_RECEIVE_ID_TYPE",
    ):
        monkeypatch.delenv(name, raising=False)

    creds = load_credentials_from_env()

    assert creds.app_id == ""
    assert creds.app_secret == ""
    assert creds.receive_id == ""
    assert creds.receive_id_type == "chat_id"


def test_channel_override_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERTPUSH_APP_ID", "cli_x")
    monkeypatch.setenv("ALERTPUSH_APP_SECRET", "s_x")
    monkeypatch.setenv("FEISHU_ALARM_RECEIVE_ID", "oc_env")

    creds = load_credentials_from_env(channel_override="oc_arg")

    assert creds.receive_id == "oc_arg"
