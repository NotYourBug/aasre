"""Approval broker terminal-state and abandonment semantics."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, cast

import pytest

from gateway.core.middleware import approvals as approvals_module
from gateway.core.middleware.approvals import ApprovalBroker


class _Clock:
    def __init__(self, now: float = 10.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _ResolveAfterDeadlineEvent:
    """Attempt a resolution after the wait deadline but before it returns."""

    def __init__(self, clock: _Clock, resolve: Callable[[], bool]) -> None:
        self._clock = clock
        self._resolve = resolve
        self._event = threading.Event()
        self.resolve_result: bool | None = None

    def wait(self, timeout: float | None = None) -> bool:
        self._clock.now += timeout or 0.0
        self.resolve_result = self._resolve()
        return False

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


def test_timeout_wins_before_a_late_resolve_can_finalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions, _capture = _capture_actions(monkeypatch)
    clock = _Clock()
    broker = ApprovalBroker(clock=clock)
    approval_id = broker.create(platform="feishu", chat_id="oc_1")
    coordinated = _ResolveAfterDeadlineEvent(
        clock,
        lambda: broker.resolve(approval_id, approved=True, decided_by="ou_1"),
    )
    broker._pending[approval_id].event = cast(Any, coordinated)  # noqa: SLF001

    result = broker.wait(approval_id, timeout=5.0)

    assert coordinated.resolve_result is False
    assert result == (False, "")
    assert [action for action in actions if action.startswith("approval.")] == [
        "approval.create",
        "approval.expire",
    ]
