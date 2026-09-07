"""Tests for integrations.feishu.credentials."""

from __future__ import annotations

import os

import pytest

from integrations.feishu.credentials import FeishuAlarmCredentials, load_credentials_from_env


def _stub_credential_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the credentials-file tier so env-only tests stay hermetic."""
    monkeypatch.setattr(
        "config.llm_credentials.resolve_env_credential",
        lambda env_var, default="": os.environ.get(env_var, default),
    )


def test_load_credentials_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_credential_resolver(monkeypatch)
    monkeypatch.setenv("ALERTPUSH_APP_ID", "cli_x")
    monkeypatch.setenv("ALERTPUSH_APP_SECRET", "s_x")
    monkeypatch.setenv("FEISHU_ALARM_RECEIVE_ID", "oc_x")
    monkeypatch.setenv("FEISHU_ALARM_RECEIVE_ID_TYPE", "open_id")

    creds = load_credentials_from_env()

    assert creds == FeishuAlarmCredentials(
        app_id="cli_x", app_secret="s_x", receive_id="oc_x", receive_id_type="open_id"
    )


def test_load_credentials_returns_empty_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_credential_resolver(monkeypatch)
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
    _stub_credential_resolver(monkeypatch)
    monkeypatch.setenv("ALERTPUSH_APP_ID", "cli_x")
    monkeypatch.setenv("ALERTPUSH_APP_SECRET", "s_x")
    monkeypatch.setenv("FEISHU_ALARM_RECEIVE_ID", "oc_env")

    creds = load_credentials_from_env(channel_override="oc_arg")

    assert creds.receive_id == "oc_arg"


def test_load_credentials_resolves_secret_via_credential_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app secret resolves env-first then the credentials file (Greptile)."""
    monkeypatch.delenv("ALERTPUSH_APP_SECRET", raising=False)
    monkeypatch.setattr(
        "config.llm_credentials.resolve_env_credential",
        lambda env_var, default="": "from_file" if env_var == "ALERTPUSH_APP_SECRET" else default,
    )

    creds = load_credentials_from_env()

    assert creds.app_secret == "from_file"
