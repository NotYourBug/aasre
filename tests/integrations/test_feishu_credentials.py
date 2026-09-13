"""Tests for integrations.feishu.credentials."""

from __future__ import annotations

import os

import pytest

from integrations.feishu import credentials
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


def test_chat_credentials_prefer_the_integration_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guided setup writes the store; the store must win over a stale env var."""
    monkeypatch.setenv("FEISHU_APP_ID", "cli_from_env")
    monkeypatch.setenv("FEISHU_CHAT_RECEIVE_ID", "oc_from_env")
    monkeypatch.setattr(
        credentials,
        "_feishu_store_config",
        lambda: {
            "app_id": "cli_from_store",
            "app_secret": "s_from_store",
            "receive_id": "oc_from_store",
            "receive_id_type": "open_id",
        },
    )

    resolved = credentials.load_chat_credentials_from_env()

    assert resolved.app_id == "cli_from_store"
    assert resolved.app_secret == "s_from_store"
    assert resolved.receive_id == "oc_from_store"
    assert resolved.receive_id_type == "open_id"


def test_chat_credentials_fall_back_to_env_when_the_store_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A .env-only deployment must keep working untouched."""
    monkeypatch.setattr(credentials, "_feishu_store_config", dict)
    monkeypatch.setenv("FEISHU_APP_ID", "cli_from_env")
    monkeypatch.setenv("FEISHU_CHAT_RECEIVE_ID", "oc_from_env")
    monkeypatch.setattr(
        "config.llm_credentials.resolve_env_credential", lambda _name, **_kw: "s_from_env"
    )

    resolved = credentials.load_chat_credentials_from_env()

    assert resolved.app_id == "cli_from_env"
    assert resolved.app_secret == "s_from_env"
    assert resolved.receive_id == "oc_from_env"


def test_alert_push_credentials_stay_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The alert-push app is not catalogued; its resolution must not read the store."""
    monkeypatch.setattr(credentials, "_feishu_store_config", dict)
    monkeypatch.setenv("ALERTPUSH_APP_ID", "cli_alert")
    monkeypatch.setenv("FEISHU_ALARM_RECEIVE_ID", "oc_alarm")
    monkeypatch.setattr(
        "config.llm_credentials.resolve_env_credential", lambda _name, **_kw: "s_alert"
    )

    resolved = credentials.load_credentials_from_env()

    assert resolved.app_id == "cli_alert"
    assert resolved.app_secret == "s_alert"
    assert resolved.receive_id == "oc_alarm"


def test_chat_credentials_resolve_allowed_open_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gateway transport reads the allowlist through this leaf, so it must carry it."""
    monkeypatch.setattr(
        credentials,
        "_feishu_store_config",
        lambda: {"app_id": "cli_1", "allowed_open_ids": "ou_store"},
    )

    assert credentials.load_chat_credentials_from_env().allowed_open_ids == "ou_store"


def test_store_lookup_failure_degrades_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken catalog read must not take the gateway down at import time."""

    def _boom() -> dict[str, object]:
        raise RuntimeError("catalog offline")

    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", _boom)

    assert credentials._feishu_store_config() == {}
