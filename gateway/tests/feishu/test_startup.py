"""Feishu startup gates on credentials before starting the worker."""

from __future__ import annotations

import pytest

from gateway.core.lifecycle.errors import GatewayConfigurationError
from gateway.transports.feishu.startup import start_feishu_worker


def test_missing_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    with pytest.raises(GatewayConfigurationError):
        start_feishu_worker(logger=None, handler=None)
