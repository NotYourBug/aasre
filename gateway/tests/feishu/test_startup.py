"""Feishu startup gates on credentials before starting the worker."""

from __future__ import annotations

import pytest
from lark_oapi.core.exception import ObtainAccessTokenException

from gateway.core.lifecycle.errors import (
    GatewayConfigurationError,
    GatewayTransportFailedError,
)
from gateway.transports.feishu.startup import start_feishu_worker


def test_missing_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    with pytest.raises(GatewayConfigurationError):
        start_feishu_worker(logger=None, handler=None)


def test_bad_credentials_raise_transport_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "s_test")

    def _raise_verify(_app_id: str, _app_secret: str) -> None:
        raise ObtainAccessTokenException("bad creds", 0, "auth failed")

    monkeypatch.setattr(
        "gateway.transports.feishu.background._verify_feishu_credentials",
        _raise_verify,
    )
    with pytest.raises(GatewayTransportFailedError):
        start_feishu_worker(logger=None, handler=None)
