import json

import pytest

from config.constants import INTEGRATIONS_STORE_PATH_ENV
from gateway.core.lifecycle.errors import GatewayConfigurationError
from gateway.transports.feishu import settings
from gateway.transports.feishu.settings import load_feishu_gateway_settings
from integrations.feishu.credentials import FeishuChatCredentials, load_chat_credentials_from_env


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    with pytest.raises(GatewayConfigurationError):
        load_feishu_gateway_settings()


def test_loads_credentials(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "s_test")
    settings = load_feishu_gateway_settings()
    assert settings.app_id == "cli_test"
    assert settings.app_secret == "s_test"


def test_loads_allowed_open_ids_from_env(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "s_test")
    monkeypatch.setenv("FEISHU_ALLOWED_OPEN_IDS", "ou_alice, ou_bob ,ou_carol")
    settings = load_feishu_gateway_settings()
    assert settings.allowed_open_ids == ["ou_alice", "ou_bob", "ou_carol"]


def test_store_credentials_start_the_worker_without_env(monkeypatch):
    """Guided setup writes the store; the gateway must start from it alone."""
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_ALLOWED_OPEN_IDS", raising=False)
    monkeypatch.setattr(
        settings,
        "load_chat_credentials_from_env",
        lambda: FeishuChatCredentials(
            app_id="cli_store",
            app_secret="s_store",
            receive_id="oc_store",
            receive_id_type="chat_id",
            allowed_open_ids="ou_store",
        ),
    )

    loaded = settings.load_feishu_gateway_settings()

    assert loaded.app_id == "cli_store"
    assert loaded.app_secret == "s_store"
    assert loaded.allowed_open_ids == ["ou_store"]


def test_env_still_configures_the_worker_when_the_store_is_empty(monkeypatch):
    """A .env-only deployment must keep working untouched."""
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "s_env")
    monkeypatch.setenv("FEISHU_ALLOWED_OPEN_IDS", "ou_env")

    loaded = settings.load_feishu_gateway_settings()

    assert loaded.app_id == "cli_env"
    assert loaded.app_secret == "s_env"
    assert loaded.allowed_open_ids == ["ou_env"]


def test_empty_credential_values_raise_instead_of_starting_a_broken_worker(monkeypatch):
    """A half-configured store yields present-but-empty values; that must not start the worker."""
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.setattr(
        settings,
        "load_chat_credentials_from_env",
        lambda: FeishuChatCredentials(
            app_id="cli_partial",
            app_secret="",
            receive_id="",
            receive_id_type="chat_id",
            allowed_open_ids="",
        ),
    )

    with pytest.raises(GatewayConfigurationError):
        settings.load_feishu_gateway_settings()


def test_a_store_record_alone_starts_the_worker_end_to_end(monkeypatch, tmp_path):
    """The spec's chain on the real store file: record → effective view → leaf → settings.

    Every other test here stubs one hop, so a key-name drift between the
    classifier, the effective view, and the leaf would pass all of them.
    """
    store = tmp_path / "integrations.json"
    store.write_text(
        json.dumps(
            {
                "version": 2,
                "integrations": [
                    {
                        "id": "feishu-e2e",
                        "service": "feishu",
                        "status": "active",
                        "instances": [
                            {
                                "name": "default",
                                "tags": {},
                                "credentials": {
                                    "app_id": "cli_from_store",
                                    "app_secret": "s_from_store",
                                    "receive_id": "oc_from_store",
                                    "receive_id_type": "chat_id",
                                    "allowed_open_ids": "ou_from_store",
                                },
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(INTEGRATIONS_STORE_PATH_ENV, str(store))
    for name in (
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_ALLOWED_OPEN_IDS",
        "FEISHU_CHAT_RECEIVE_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    # The gateway settings surface only three of the five keys, so assert the
    # leaf directly for the other two — a drift in ``receive_id`` or
    # ``receive_id_type`` between the classifier and the leaf would otherwise
    # pass here and fail only at delivery.
    creds = load_chat_credentials_from_env()

    assert creds.app_id == "cli_from_store"
    assert creds.app_secret == "s_from_store"
    assert creds.receive_id == "oc_from_store"
    assert creds.receive_id_type == "chat_id"
    assert creds.allowed_open_ids == "ou_from_store"

    loaded = load_feishu_gateway_settings()

    assert loaded.app_id == "cli_from_store"
    assert loaded.app_secret == "s_from_store"
    assert loaded.allowed_open_ids == ["ou_from_store"]
