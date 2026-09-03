"""Tests for integrations.feishu.alarms.FeishuAlarmDispatcher."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.feishu.alarms import FeishuAlarmCredentials, FeishuAlarmDispatcher

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


def _stub_create_message(
    monkeypatch: pytest.MonkeyPatch, *, ok: bool = True, msg: str = "success"
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_create(creds: FeishuAlarmCredentials, text: str) -> object:
        calls.append({"creds": creds, "text": text})
        # ``success`` is a bound method on the response, so it receives the
        # instance as its first argument — mirror the SDK's method shape.
        return type("R", (), {"success": lambda _self: ok, "code": 0 if ok else 1, "msg": msg})()

    monkeypatch.setattr("integrations.feishu.alarms._create_message", _fake_create)
    return calls


def test_dispatch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_create_message(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = FeishuAlarmDispatcher(_CREDS)

    assert dispatcher.dispatch("max_cpu", "alarm body") is True
    assert len(calls) == 1
    assert calls[0]["text"] == "[max_cpu] alarm body"


def test_dispatch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_create_message(monkeypatch, ok=False, msg="denied")
    _patch_clock(monkeypatch, [100.0])

    dispatcher = FeishuAlarmDispatcher(_CREDS)

    assert dispatcher.dispatch("max_cpu", "alarm body") is False
    assert len(calls) == 1


def test_second_dispatch_within_cooldown_is_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_create_message(monkeypatch)
    _patch_clock(monkeypatch, [100.0, 200.0])

    dispatcher = FeishuAlarmDispatcher(_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "first") is True
    assert dispatcher.dispatch("max_cpu", "second") is False
    assert len(calls) == 1
    assert calls[0]["text"] == "[max_cpu] first"


def test_dispatch_transport_exception_returns_false_and_keeps_cooldown_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(creds: FeishuAlarmCredentials, text: str) -> object:
        raise RuntimeError("network exploded")

    monkeypatch.setattr("integrations.feishu.alarms._create_message", _raise)
    _patch_clock(monkeypatch, [100.0, 105.0])

    dispatcher = FeishuAlarmDispatcher(_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "first") is False
    # Cooldown stayed armed after the raise — the second call within the
    # window is suppressed rather than retrying immediately.
    assert dispatcher.dispatch("max_cpu", "second") is False
