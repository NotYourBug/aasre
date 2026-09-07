"""Tests for integrations.feishu.background_adapter."""

from __future__ import annotations

from typing import Any

import pytest

from core.domain.background_investigations import BackgroundInvestigationRecord
from integrations.feishu.background_adapter import deliver_feishu_notification


def _record() -> BackgroundInvestigationRecord:
    return BackgroundInvestigationRecord(
        task_id="bg-1",
        status="completed",
        command="/investigate x",
        root_cause="boom",
        top_analysis=("a",),
        next_steps=("b",),
        stats={"tool_call_count": 1},
    )


def _creds(
    *,
    app_id: str = "cli_x",
    app_secret: str = "s_x",
    receive_id: str = "oc_x",
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


def test_missing_credentials_returns_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.feishu.credentials.load_credentials_from_env",
        lambda: _creds(app_id="", app_secret="", receive_id=""),
    )

    outcome = deliver_feishu_notification(_record())

    assert outcome.startswith("missing feishu integration")


def test_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.feishu.credentials.load_credentials_from_env",
        lambda: _creds(),
    )
    monkeypatch.setattr(
        "integrations.feishu.delivery.send_feishu_report",
        lambda _body, _ctx: (True, ""),
    )

    assert deliver_feishu_notification(_record()) == "sent"


def test_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.feishu.credentials.load_credentials_from_env",
        lambda: _creds(),
    )
    monkeypatch.setattr(
        "integrations.feishu.delivery.send_feishu_report",
        lambda _body, _ctx: (False, "denied"),
    )

    assert deliver_feishu_notification(_record()) == "failed: denied"
