import pytest

from gateway.core.lifecycle.errors import GatewayConfigurationError
from gateway.transports.feishu.settings import load_feishu_gateway_settings


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
