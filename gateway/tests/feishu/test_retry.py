"""Bounded Feishu retry callback admission and reauthorization."""

from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import pytest

from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.reply_actions import ReplyActionRegistry, ReplyActionState
from gateway.transports.feishu.retry import FeishuRetryService


def _callback(
    token: object = "token",
    *,
    actor: object = "actor",
    chat: object = "chat",
    tag: str = "button",
    value: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                tag=tag,
                value={"retry_id": token} if value is None else value,
            ),
            operator=SimpleNamespace(open_id=actor),
            context=SimpleNamespace(open_chat_id=chat),
        )
    )


def _ready_registry() -> tuple[ReplyActionRegistry, str, str]:
    registry = ReplyActionRegistry(token_factory=lambda: "token")
    handle = registry.begin(
        FeishuInboundMessage("chat", "actor", "source", "prompt"),
        prompt="normalized prompt",
        session_id="session",
    )
    assert handle is not None
    registry.observe_message(handle.generation_id, "answer")
    assert (
        registry.complete(
            handle.generation_id,
            final_message_id="answer",
            card_id="card",
            next_sequence=3,
        )
        is not None
    )
    return registry, handle.generation_id, handle.retry_token


def test_callback_returns_before_blocked_authorization_and_dispatches_once() -> None:
    registry, generation_id, token = _ready_registry()
    entered, release, dispatched = Event(), Event(), []

    def authorized(_actor: str, _chat: str) -> bool:
        entered.set()
        assert release.wait(5)
        return True

    service = FeishuRetryService(
        reply_actions=registry,
        authorized=authorized,
        current_session_id=lambda _actor, _chat: "session",
        dispatch=lambda prepared: dispatched.append(prepared) or True,
    )
    try:
        response = service.handle_action(_callback(token))
        assert response.toast.content == "已收到"
        assert entered.wait(5)
        assert dispatched == []
    finally:
        release.set()
        service.shutdown(timeout_seconds=5)

    assert len(dispatched) == 1
    assert dispatched[0].prompt == "normalized prompt"
    assert registry.state(generation_id) is ReplyActionState.RETRY_DISPATCHED


@pytest.mark.parametrize(
    "callback",
    [
        SimpleNamespace(event=None),
        SimpleNamespace(event=SimpleNamespace(operator=None, context=None, action=None)),
        _callback(tag="select_static"),
        _callback(value={"retry_id": "token", "extra": "x"}),
        _callback(value={"feedback_id": "token"}),
        _callback(""),
        _callback("x" * 257),
        _callback(actor=""),
        _callback(chat=""),
    ],
)
def test_malformed_callback_fails_closed(callback: SimpleNamespace) -> None:
    registry, _generation_id, _token = _ready_registry()
    service = FeishuRetryService(
        reply_actions=registry,
        authorized=lambda _actor, _chat: True,
        current_session_id=lambda _actor, _chat: "session",
        dispatch=lambda _prepared: True,
    )
    try:
        response = service.handle_action(callback)
    finally:
        service.shutdown(timeout_seconds=5)
    assert response.toast.content == "此操作暂不可用"


def test_wrong_authority_and_session_do_not_consume_rightful_retry() -> None:
    registry, generation_id, token = _ready_registry()
    dispatched: list[object] = []
    service = FeishuRetryService(
        reply_actions=registry,
        authorized=lambda actor, _chat: actor == "actor",
        current_session_id=lambda _actor, chat: "session" if chat == "chat" else "wrong",
        dispatch=lambda prepared: dispatched.append(prepared) or True,
    )
    for callback in (_callback(token, actor="wrong"), _callback(token, chat="wrong")):
        service.handle_action(callback)
    service.shutdown(timeout_seconds=5)
    assert dispatched == []
    assert registry.state(generation_id) is ReplyActionState.READY


def test_queue_full_returns_retry_later_without_claiming() -> None:
    registry, generation_id, token = _ready_registry()
    entered, release = Event(), Event()

    def authorized(_actor: str, _chat: str) -> bool:
        entered.set()
        assert release.wait(5)
        return True

    service = FeishuRetryService(
        reply_actions=registry,
        authorized=authorized,
        current_session_id=lambda _actor, _chat: "session",
        dispatch=lambda _prepared: True,
        queue_limit=1,
    )
    try:
        assert service.handle_action(_callback(token)).toast.content == "已收到"
        assert entered.wait(5)
        assert service.handle_action(_callback(token)).toast.content == "暂时繁忙，请稍后重试"
        assert registry.state(generation_id) is ReplyActionState.READY
    finally:
        release.set()
        service.shutdown(timeout_seconds=5)
