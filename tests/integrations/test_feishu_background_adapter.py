"""Tests for integrations.feishu.background_adapter."""

from __future__ import annotations

from typing import Any

import pytest

from core.domain.background_investigations import BackgroundInvestigationRecord
from integrations.feishu.background_adapter import deliver_feishu_notification
from integrations.feishu.delivery_types import (
    FeishuDeliveryErrorCategory,
    FeishuDeliveryMode,
    FeishuDeliveryStatus,
)
from integrations.feishu.document_delivery import FeishuDocumentDeliveryResult


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


def _result(
    status: FeishuDeliveryStatus,
    *,
    error: str = "safe error",
) -> FeishuDocumentDeliveryResult:
    return FeishuDocumentDeliveryResult(
        status=status,
        attempted=status is not FeishuDeliveryStatus.SKIPPED,
        confirmed_message_ids=("om_1",) if status is not FeishuDeliveryStatus.FAILED else (),
        delivery_mode=FeishuDeliveryMode.CARDS,
        error_category=(
            FeishuDeliveryErrorCategory.INTERNAL if status is FeishuDeliveryStatus.FAILED else None
        ),
        error=error,
    )


@pytest.mark.parametrize(
    "status",
    [FeishuDeliveryStatus.SUCCESS, FeishuDeliveryStatus.DEGRADED_SUCCESS],
)
def test_success_and_degraded_are_sent(
    monkeypatch: pytest.MonkeyPatch,
    status: FeishuDeliveryStatus,
) -> None:
    monkeypatch.setattr(
        "integrations.feishu.credentials.load_credentials_from_env",
        lambda: _creds(),
    )
    monkeypatch.setattr(
        "integrations.feishu.document_delivery.deliver_feishu_document",
        lambda **_kwargs: _result(status),
    )

    assert deliver_feishu_notification(_record()) == "sent"


def test_failed_is_fixed_and_does_not_expose_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.feishu.credentials.load_credentials_from_env",
        lambda: _creds(),
    )
    monkeypatch.setattr(
        "integrations.feishu.document_delivery.deliver_feishu_document",
        lambda **_kwargs: _result(
            FeishuDeliveryStatus.FAILED,
            error="SECRET body https://target Authorization: Bearer token",
        ),
    )

    assert deliver_feishu_notification(_record()) == "failed: Feishu delivery failed"
