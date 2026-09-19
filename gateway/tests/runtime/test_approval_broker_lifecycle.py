"""Approval broker terminal-state and abandonment semantics."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, cast

import pytest

from gateway.core.middleware import approvals as approvals_module
from gateway.core.middleware.approvals import ApprovalBroker


class _TimeoutThenResolveEvent:
    """Report a timeout only after a concurrent resolver has set the event."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._barrier = threading.Barrier(2)

    def wait(self, _timeout: float | None = None) -> bool:
        self._barrier.wait(timeout=1.0)
        self._barrier.wait(timeout=1.0)
        return False

    def wait_until_waiting(self) -> None:
        self._barrier.wait(timeout=1.0)

    def release_waiter(self) -> None:
        self._barrier.wait(timeout=1.0)

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        self._event.set()


def _capture_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], Callable[..., None]]:
    actions: list[str] = []

    def _capture(**fields: Any) -> None:
        actions.append(str(fields["action"]))

    monkeypatch.setattr(approvals_module, "audit_security_action", _capture)
    return actions, _capture


def test_abandon_releases_waiter_without_expiry_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions, _capture = _capture_actions(monkeypatch)
    broker = ApprovalBroker()
    approval_id = broker.create(platform="feishu", chat_id="oc_1")
    result: list[tuple[bool, str]] = []
    waiting = threading.Event()

    def _wait() -> None:
        waiting.set()
        result.append(broker.wait(approval_id, timeout=5.0))

    thread = threading.Thread(target=_wait)
    thread.start()
    assert waiting.wait(timeout=1.0)

    assert broker.abandon(approval_id) is True
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert result == [(False, "")]
    assert actions == ["approval.create", "approval.abandon"]


def test_abandon_unknown_or_completed_approval_is_safe() -> None:
    broker = ApprovalBroker()
    approval_id = broker.create()

    assert broker.abandon("missing") is False
    assert broker.abandon(approval_id) is True
    assert broker.abandon(approval_id) is False


def test_resolve_wins_when_event_is_set_before_timeout_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions, _capture = _capture_actions(monkeypatch)
    broker = ApprovalBroker()
    approval_id = broker.create(platform="feishu", chat_id="oc_1")
    coordinated = _TimeoutThenResolveEvent()
    broker._pending[approval_id].event = cast(Any, coordinated)  # noqa: SLF001
    result: list[tuple[bool, str]] = []

    thread = threading.Thread(target=lambda: result.append(broker.wait(approval_id, timeout=0.0)))
    thread.start()
    coordinated.wait_until_waiting()

    assert broker.resolve(approval_id, approved=True, decided_by="ou_1") is True
    coordinated.release_waiter()
    thread.join(timeout=1.0)

    assert result == [(True, "ou_1")]
    assert [action for action in actions if action.startswith("approval.")] == [
        "approval.create",
        "approval.resolve",
    ]
