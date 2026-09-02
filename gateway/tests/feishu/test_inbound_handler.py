"""Feishu turn pipeline: rotation, credit metering, timeout, and /stop cancel.

These pin the dispatcher/arbiter wiring that Telegram already covers — a
``/new`` rotates without running the agent, a credit denial never reaches the
agent, a soft timeout finalizes the output, and a ``/stop`` is honoured via a
cancel Event that was registered at dispatch time (R19).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from config.constants.gateway import (
    CREDITS_DENIED_MESSAGE,
    ROTATE_SESSION,
    TURN_TIMEOUT_MESSAGE,
    USER_STOP_MESSAGE,
)
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from gateway.core.billing import turn_metering
from gateway.core.billing.credits_client import CreditsOutcome
from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.conversation_locks import ConversationLockRegistry
from gateway.tests.billing.turn_metering_harness import metered_callback
from gateway.transports.feishu import inbound_handler
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.inbound_handler import handle_inbound_feishu_message
from gateway.transports.feishu.inbound_security import FeishuInboundDecision
from gateway.transports.feishu.session_rotation import conversation_key
from gateway.transports.feishu.settings import FeishuGatewaySettings

TEST_ORG_ID = "org_feishu_turn"
LOGGER = logging.getLogger("gateway.test")


def _settings(**kwargs: Any) -> FeishuGatewaySettings:
    defaults: dict[str, Any] = {
        "app_id": "app",
        "app_secret": "secret",
        "allowed_open_ids": ["ou_user-1"],
    }
    defaults.update(kwargs)
    return FeishuGatewaySettings(**defaults)


def _inbound(
    text: str = "hello",
    *,
    chat_id: str = "oc_chat-1",
    open_id: str = "ou_user-1",
) -> FeishuInboundMessage:
    return FeishuInboundMessage(chat_id=chat_id, open_id=open_id, message_id="m1", text=text)


class _FakeSessionResolver:
    def __init__(self, session: SessionCore) -> None:
        self._session = session
        self.rotated = False
        self.resolved = False

    def resolve(self, **_kwargs: object) -> SessionCore:
        self.resolved = True
        return self._session

    def rotate(self, **_kwargs: object) -> SessionCore:
        self.rotated = True
        return self._session


@pytest.fixture(autouse=True)
def _authorized_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORGANIZATION_ID", TEST_ORG_ID)
    monkeypatch.setattr(
        inbound_handler,
        "enforce_inbound_feishu_message_security",
        lambda **_kwargs: FeishuInboundDecision(allowed=True),
    )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    inbound: FeishuInboundMessage,
    handler: Any,
    resolver: _FakeSessionResolver,
    settings: FeishuGatewaySettings,
    active_cancels: ActiveTurnRegistry,
    turn_cancel: threading.Event | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Run one turn synchronously; returns (outbound sends, chat replies)."""
    outbound: list[tuple[str, str]] = []
    replies: list[tuple[str, str]] = []

    def fake_send(_app_id: str, _app_secret: str, chat_id: str, text: str) -> None:
        outbound.append((chat_id, text))

    def send_text(chat_id: str, text: str) -> None:
        replies.append((chat_id, text))

    monkeypatch.setattr("gateway.transports.feishu.turn_output._send_text", fake_send)

    handle_inbound_feishu_message(
        inbound,
        settings=settings,
        session_resolver=resolver,  # type: ignore[arg-type]
        active_cancels=active_cancels,
        conversation_locks=ConversationLockRegistry(),
        send_text=send_text,
        handler=handler,
        logger=LOGGER,
        turn_cancel=turn_cancel,
    )
    return outbound, replies


def test_new_rotation_short_circuits_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        inbound_handler,
        "enforce_inbound_feishu_message_security",
        lambda **_kwargs: FeishuInboundDecision(allowed=True, reply_text=ROTATE_SESSION),
    )
    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    callback = MagicMock()

    _outbound, _replies = _run(
        monkeypatch,
        inbound=_inbound("/new"),
        handler=callback,
        resolver=resolver,
        settings=_settings(),
        active_cancels=ActiveTurnRegistry(),
    )

    assert resolver.rotated is True
    assert resolver.resolved is False
    callback.assert_not_called()


def test_denied_credits_stop_the_turn_before_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny(_organization_id: str, *, reason: str, **_kwargs: object) -> CreditsOutcome:
        _ = reason
        return CreditsOutcome.DENIED

    monkeypatch.setattr(turn_metering, "consume_credits", deny)
    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    callback = MagicMock()

    outbound, _replies = _run(
        monkeypatch,
        inbound=_inbound("hello"),
        handler=metered_callback(callback),
        resolver=resolver,
        settings=_settings(),
        active_cancels=ActiveTurnRegistry(),
        turn_cancel=threading.Event(),
    )

    callback.assert_not_called()
    assert outbound[-1][1] == CREDITS_DENIED_MESSAGE


def test_turn_timeout_finalizes_output_and_sets_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    seen_cancel: list[threading.Event] = []

    def hanging_handler(
        _text: str,
        _session: Any,
        sink: Any,
        _logger: logging.Logger,
    ) -> None:
        cancel = getattr(sink, "turn_cancel", None)
        assert isinstance(cancel, threading.Event)
        seen_cancel.append(cancel)
        release.wait(5.0)

    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    outbound: list[tuple[str, str]] = []

    def fake_send(_app_id: str, _app_secret: str, _chat_id: str, text: str) -> None:
        outbound.append((_chat_id, text))

    monkeypatch.setattr("gateway.transports.feishu.turn_output._send_text", fake_send)

    error: list[Exception] = []
    done = threading.Event()

    def _thread() -> None:
        try:
            handle_inbound_feishu_message(
                _inbound("hello"),
                settings=_settings(turn_timeout_seconds=0.05),
                session_resolver=resolver,  # type: ignore[arg-type]
                active_cancels=ActiveTurnRegistry(),
                conversation_locks=ConversationLockRegistry(),
                send_text=lambda _c, _t: None,
                handler=hanging_handler,
                logger=LOGGER,
            )
        except Exception as exc:  # pragma: no cover - failure path
            error.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_thread)
    worker.start()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not any(
        TURN_TIMEOUT_MESSAGE in text for _, text in outbound
    ):
        time.sleep(0.02)
    release.set()
    assert done.wait(5.0)
    worker.join(5.0)
    assert not error, error
    assert any(TURN_TIMEOUT_MESSAGE in text for _, text in outbound), outbound
    assert seen_cancel and seen_cancel[0].is_set()


def test_pre_registered_cancel_short_circuits_before_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A /stop between dispatch and the turn body is honoured (R19)."""
    turn_cancel = threading.Event()
    registry = ActiveTurnRegistry()
    inbound = _inbound("hello")
    key = conversation_key(inbound)
    registry.register(key, turn_cancel)  # dispatch-time registration
    assert registry.request_stop(key) is True  # /stop arrives before the body

    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    callback = MagicMock()

    outbound, _replies = _run(
        monkeypatch,
        inbound=inbound,
        handler=callback,
        resolver=resolver,
        settings=_settings(),
        active_cancels=registry,
        turn_cancel=turn_cancel,
    )

    callback.assert_not_called()
    assert turn_cancel.is_set()
    assert outbound[-1][1] == USER_STOP_MESSAGE


def test_in_flight_stop_cancels_the_turn_via_pre_registered_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A /stop during the agent cancels it through the dispatch-time Event (R19)."""
    turn_cancel = threading.Event()
    registry = ActiveTurnRegistry()
    inbound = _inbound("hello")
    key = conversation_key(inbound)
    registry.register(key, turn_cancel)

    release = threading.Event()
    agent_started = threading.Event()
    callback = MagicMock()

    def cooperative_agent(
        _text: str,
        _session: Any,
        sink: Any,
        _logger: logging.Logger,
    ) -> None:
        agent_started.set()
        cancel = sink.turn_cancel
        while not cancel.is_set():
            if release.wait(0.05):
                break
        if not cancel.is_set():
            callback(_text, _session, sink, _logger)

    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    outbound: list[tuple[str, str]] = []

    def fake_send(_app_id: str, _app_secret: str, _chat_id: str, text: str) -> None:
        outbound.append((_chat_id, text))

    monkeypatch.setattr("gateway.transports.feishu.turn_output._send_text", fake_send)

    error: list[Exception] = []
    done = threading.Event()

    def _thread() -> None:
        try:
            handle_inbound_feishu_message(
                inbound,
                settings=_settings(),
                session_resolver=resolver,  # type: ignore[arg-type]
                active_cancels=registry,
                conversation_locks=ConversationLockRegistry(),
                send_text=lambda _c, _t: None,
                handler=cooperative_agent,
                logger=LOGGER,
                turn_cancel=turn_cancel,
            )
        except Exception as exc:  # pragma: no cover - failure path
            error.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_thread)
    worker.start()
    assert agent_started.wait(2.0)
    assert registry.request_stop(key) is True
    release.set()
    assert done.wait(5.0)
    worker.join(5.0)
    assert not error, error
    assert turn_cancel.is_set()
    callback.assert_not_called()
    assert any(USER_STOP_MESSAGE in text for _, text in outbound), outbound
