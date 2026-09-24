"""Tests for integrations.feishu.reporting_adapter."""

from __future__ import annotations

from typing import Any

import pytest

import integrations.feishu.reporting_adapter  # noqa: F401  (import registers the adapter)
from infrastructure.delivery.reporting.delivery_registry import (
    get_delivery_adapter,
    registered_delivery_adapter_names,
)
from integrations.feishu.delivery_types import (
    FeishuDeliveryErrorCategory,
    FeishuDeliveryMode,
    FeishuDeliveryStatus,
)
from integrations.feishu.document_delivery import FeishuDocumentDeliveryResult


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


def _install_fake_document_delivery(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: FeishuDocumentDeliveryResult,
) -> list[dict[str, Any]]:
    """Patch the document owner and capture its complete safe input."""
    calls: list[dict[str, Any]] = []

    def _fake_deliver(**kwargs: Any) -> FeishuDocumentDeliveryResult:
        calls.append(kwargs)
        return result

    monkeypatch.setattr(
        "integrations.feishu.document_delivery.deliver_feishu_document", _fake_deliver
    )
    return calls


def _result(status: FeishuDeliveryStatus) -> FeishuDocumentDeliveryResult:
    return FeishuDocumentDeliveryResult(
        status=status,
        attempted=status is not FeishuDeliveryStatus.SKIPPED,
        confirmed_message_ids=("om_1",) if status is not FeishuDeliveryStatus.FAILED else (),
        delivery_mode=FeishuDeliveryMode.CARDS,
        error_category=(
            FeishuDeliveryErrorCategory.INTERNAL
            if status is FeishuDeliveryStatus.FAILED
            else None
        ),
        error="safe failure",
    )


def _deliver(messages: dict[str, Any]) -> bool:
    adapter = get_delivery_adapter("feishu")
    assert adapter is not None
    return adapter.deliver({}, messages=messages, blocks=[])


def test_registers_under_the_feishu_name() -> None:
    assert "feishu" in registered_delivery_adapter_names()


def test_missing_credentials_skips_without_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_chat_creds(monkeypatch, _chat_creds(app_id="", app_secret="", receive_id=""))

    assert _deliver({"markdown_text": "report"}) is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (FeishuDeliveryStatus.SUCCESS, True),
        (FeishuDeliveryStatus.DEGRADED_SUCCESS, True),
        (FeishuDeliveryStatus.FAILED, True),
        (FeishuDeliveryStatus.SKIPPED, False),
    ],
)
def test_document_delivery_statuses_count_as_attempted(
    monkeypatch: pytest.MonkeyPatch,
    status: FeishuDeliveryStatus,
    expected: bool,
) -> None:
    _install_fake_chat_creds(monkeypatch, _chat_creds())
    calls = _install_fake_document_delivery(monkeypatch, result=_result(status))

    assert _deliver({"markdown_text": "# canonical", "slack_text": "vendor text"}) is expected
    assert len(calls) == 1
    assert calls[0]["markdown"] == "# canonical"
    assert calls[0]["app_id"] == "cli_chat"
