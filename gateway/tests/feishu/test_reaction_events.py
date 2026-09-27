"""Official lark-oapi reaction event normalization."""

from __future__ import annotations

from threading import Event
from unittest.mock import MagicMock

import pytest
from lark_oapi.api.im.v1 import (
    P2ImMessageReactionCreatedV1,
    P2ImMessageReactionDeletedV1,
)

from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.reaction_events import (
    FeishuReactionEvent,
    FeishuReactionService,
    normalize_reaction_created,
    normalize_reaction_deleted,
)
from gateway.transports.feishu.reply_actions import ReplyActionRegistry


def _payload(*, operator_type: str = "user", open_id: str = "actor", action_time: str = "10"):
    return {
        "schema": "2.0",
        "header": {
            "event_id": "event-1",
            "event_type": "im.message.reaction.created_v1",
            "create_time": "0",
            "token": "",
            "app_id": "app",
            "tenant_key": "tenant",
        },
        "event": {
            "message_id": "message-1",
            "reaction_type": {"emoji_type": "CrossMark"},
            "operator_type": operator_type,
            "user_id": {"open_id": open_id},
            "action_time": action_time,
        },
    }


def test_created_and_deleted_sdk_models_normalize_exact_fields() -> None:
    created = normalize_reaction_created(P2ImMessageReactionCreatedV1(_payload()))
    deleted = normalize_reaction_deleted(P2ImMessageReactionDeletedV1(_payload()))

    assert created is not None and deleted is not None
    assert created.created is True
    assert deleted.created is False
    assert created.event_id == "event-1"
    assert created.message_id == "message-1"
    assert created.actor_open_id == "actor"
    assert created.operator_type == "user"
    assert created.emoji_type == "CrossMark"
    assert created.action_time == 10


@pytest.mark.parametrize(
    "payload",
    [
        _payload(operator_type="app"),
        _payload(open_id=""),
        _payload(action_time="not-a-number"),
        _payload(action_time="-1"),
        _payload(open_id="x" * 257),
    ],
)
def test_malformed_or_unusable_sdk_event_fails_closed(payload: dict[str, object]) -> None:
    assert normalize_reaction_created(P2ImMessageReactionCreatedV1(payload)) is None


def test_streaming_retry_waits_for_completion_then_dispatches() -> None:
    registry = ReplyActionRegistry()
    handle = registry.begin(
        FeishuInboundMessage("chat", "actor", "source", "prompt"),
        prompt="normalized",
        session_id="session",
    )
    assert handle is not None
    registry.observe_message(handle.generation_id, "answer")
    dispatched: list[object] = []
    service = FeishuReactionService(
        reply_actions=registry,
        feedback=MagicMock(),
        authorized=lambda _actor, _chat: True,
        current_session_id=lambda _actor, _chat: "session",
        dispatch_retry=lambda prepared: dispatched.append(prepared) or True,
    )
    event = FeishuReactionEvent(
        event_id="event",
        message_id="answer",
        actor_open_id="actor",
        operator_type="user",
        emoji_type="CrossMark",
        action_time=1,
        created=True,
    )
    service.handle(event)
    assert dispatched == []

    registry.complete(
        handle.generation_id,
        final_message_id="answer",
        card_id="card",
        next_sequence=2,
    )
    service.handle_pending("chat:actor")
    service.shutdown(timeout_seconds=5)

    assert len(dispatched) == 1


def test_delete_before_completion_cancels_pending_retry() -> None:
    registry = ReplyActionRegistry()
    handle = registry.begin(
        FeishuInboundMessage("chat", "actor", "source", "prompt"),
        prompt="normalized",
        session_id="session",
    )
    assert handle is not None
    registry.observe_message(handle.generation_id, "answer")
    dispatched = Event()
    service = FeishuReactionService(
        reply_actions=registry,
        feedback=MagicMock(),
        authorized=lambda _actor, _chat: True,
        current_session_id=lambda _actor, _chat: "session",
        dispatch_retry=lambda _prepared: dispatched.set() or True,
    )
    for action_time, created in ((1, True), (2, False)):
        service.handle(
            FeishuReactionEvent(
                event_id=f"event-{action_time}",
                message_id="answer",
                actor_open_id="actor",
                operator_type="user",
                emoji_type="CrossMark",
                action_time=action_time,
                created=created,
            )
        )
    registry.complete(
        handle.generation_id,
        final_message_id="answer",
        card_id="card",
        next_sequence=2,
    )
    service.handle_pending("chat:actor")
    service.shutdown(timeout_seconds=5)

    assert not dispatched.is_set()
