"""Tests for integrations.feishu.alarms.FeishuAlarmDispatcher."""

from __future__ import annotations

import pytest

from integrations.feishu.alarms import FeishuAlarmCredentials, FeishuAlarmDispatcher
from integrations.feishu.delivery_types import (
    FeishuDeliveryErrorCategory,
    FeishuDeliveryMode,
    FeishuDeliveryStatus,
)
from integrations.feishu.document_delivery import FeishuDocumentDeliveryResult

_CREDS = FeishuAlarmCredentials(
    app_id="cli_x",
    app_secret="s_x",
    receive_id="oc_x",
    receive_id_type="chat_id",
)


def _patch_clock(monkeypatch: pytest.MonkeyPatch, ticks: list[float]) -> None:
    iterator = iter(ticks)

    def _now() -> float:
        return next(iterator)

    monkeypatch.setattr(FeishuAlarmDispatcher, "_now", staticmethod(_now))


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
        error="safe error",
    )


def _install_fake_document_delivery(
    monkeypatch: pytest.MonkeyPatch,
    result: FeishuDocumentDeliveryResult,
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def _fake_deliver(**kwargs: object) -> FeishuDocumentDeliveryResult:
        calls.append(kwargs)
        return result

    monkeypatch.setattr(
        "integrations.feishu.document_delivery.deliver_feishu_document", _fake_deliver
    )
    return calls


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (FeishuDeliveryStatus.SUCCESS, True),
        (FeishuDeliveryStatus.DEGRADED_SUCCESS, True),
        (FeishuDeliveryStatus.FAILED, False),
    ],
)
def test_dispatch_maps_whole_status_and_keeps_attempt_cooldown(
    monkeypatch: pytest.MonkeyPatch,
    status: FeishuDeliveryStatus,
    expected: bool,
) -> None:
    calls = _install_fake_document_delivery(monkeypatch, _result(status))
    _patch_clock(monkeypatch, [100.0, 105.0])

    dispatcher = FeishuAlarmDispatcher(_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "**alarm**") is expected
    assert dispatcher.dispatch("max_cpu", "**again**") is False
    assert len(calls) == 1
    assert calls[0]["markdown"] == "**[max_cpu]**\n**alarm**"


def test_missing_credentials_are_skipped_before_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_document_delivery(monkeypatch, _result(FeishuDeliveryStatus.SUCCESS))
    _patch_clock(monkeypatch, [100.0, 100.0])
    dispatcher = FeishuAlarmDispatcher(
        FeishuAlarmCredentials(app_id="", app_secret="", receive_id="")
    )

    assert dispatcher.dispatch("max_cpu", "first") is False
    dispatcher._creds = _CREDS
    assert dispatcher.dispatch("max_cpu", "second") is True
    assert len(calls) == 1


def test_blank_message_is_skipped_before_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_document_delivery(monkeypatch, _result(FeishuDeliveryStatus.SUCCESS))
    _patch_clock(monkeypatch, [100.0, 100.0])
    dispatcher = FeishuAlarmDispatcher(_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "   ") is False
    assert dispatcher.dispatch("max_cpu", "second") is True
    assert len(calls) == 1


def test_dispatch_transport_exception_returns_false_and_keeps_cooldown_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(**_kwargs: object) -> FeishuDocumentDeliveryResult:
        raise RuntimeError("network exploded")

    monkeypatch.setattr(
        "integrations.feishu.document_delivery.deliver_feishu_document", _raise
    )
    _patch_clock(monkeypatch, [100.0, 105.0])

    dispatcher = FeishuAlarmDispatcher(_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "first") is False
    # Cooldown stayed armed after the raise — the second call within the
    # window is suppressed rather than retrying immediately.
    assert dispatcher.dispatch("max_cpu", "second") is False
