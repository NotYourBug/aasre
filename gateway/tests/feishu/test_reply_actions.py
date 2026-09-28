"""In-memory Feishu reply-action lifetime, ordering, and concurrency."""

from __future__ import annotations

import threading

import pytest

from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.reply_actions import (
    ReactionAction,
    ReactionTransition,
    ReplyActionRegistry,
    ReplyActionState,
)


def _inbound(*, message_id: str = "source-message") -> FeishuInboundMessage:
    return FeishuInboundMessage(
        chat_id="chat-1",
        open_id="requester-1",
        message_id=message_id,
        text="source text",
    )


def _ready(registry: ReplyActionRegistry, *, prompt: str = "normalized prompt"):
    handle = registry.begin(_inbound(), prompt=prompt, session_id="session-1")
    registry.observe_message(handle.generation_id, "page-1")
    registry.observe_message(handle.generation_id, "page-2")
    completion = registry.complete(
        handle.generation_id,
        final_message_id="page-2",
        card_id="card-2",
        next_sequence=7,
    )
    assert completion is not None
    return handle, completion


def test_new_generation_replaces_indexes_and_retry_authority() -> None:
    registry = ReplyActionRegistry()
    first, _completion = _ready(registry)

    second = registry.begin(_inbound(message_id="new-source"), prompt="new", session_id="session-1")

    assert registry.state(first.generation_id) is None
    assert registry.message_generation("page-1") is None
    assert (
        registry.claim_retry_token(
            first.retry_token,
            actor_open_id="requester-1",
            chat_id="chat-1",
            current_session_id="session-1",
        )
        is None
    )
    assert registry.state(second.generation_id) is ReplyActionState.PREPARING


def test_overflow_messages_share_one_final_generation() -> None:
    registry = ReplyActionRegistry()
    handle, completion = _ready(registry)

    assert registry.message_generation("page-1") == handle.generation_id
    assert registry.message_generation("page-2") == handle.generation_id
    assert completion.final_message_id == "page-2"
    assert completion.card_id == "card-2"
    assert completion.next_sequence == 7
    assert registry.state(handle.generation_id) is ReplyActionState.READY


def test_uncertain_terminal_invalidates_all_indexes() -> None:
    registry = ReplyActionRegistry()
    handle = registry.begin(_inbound(), prompt="normalized", session_id="session-1")
    registry.observe_message(handle.generation_id, "page-1")

    assert registry.invalidate(handle.generation_id) is True
    assert registry.state(handle.generation_id) is None
    assert registry.message_generation("page-1") is None
    assert (
        registry.complete(
            handle.generation_id,
            final_message_id="page-1",
            card_id="card-1",
            next_sequence=2,
        )
        is None
    )


def test_expiry_restart_and_global_bound_drop_retry_capsules() -> None:
    now = [10.0]
    registry = ReplyActionRegistry(clock=lambda: now[0], expiry_seconds=5.0, max_entries=2)
    first = registry.begin(_inbound(message_id="one"), prompt="one", session_id="session-1")
    registry.begin(
        FeishuInboundMessage("chat-2", "requester-2", "two", "two"),
        prompt="two",
        session_id="session-2",
    )
    registry.begin(
        FeishuInboundMessage("chat-3", "requester-3", "three", "three"),
        prompt="three",
        session_id="session-3",
    )
    assert len(registry) == 2
    assert registry.state(first.generation_id) is None

    now[0] = 16.0
    assert len(registry) == 0
    assert len(ReplyActionRegistry()) == 0


def test_sensitive_prompt_and_raw_token_are_hidden_from_debug_repr() -> None:
    registry = ReplyActionRegistry()
    handle = registry.begin(
        _inbound(),
        prompt="sensitive attachment contents",
        session_id="session-1",
    )

    rendered = registry.debug_record(handle.generation_id)
    assert "sensitive attachment contents" not in rendered
    assert handle.retry_token not in rendered
    assert handle.retry_token not in repr(handle)


def test_normalized_prompt_replaces_preliminary_retry_content() -> None:
    registry = ReplyActionRegistry()
    handle = registry.begin(_inbound(), prompt="preliminary", session_id="session-1")

    assert registry.update_prompt(handle.generation_id, "normalized attachment content") is True
    registry.observe_message(handle.generation_id, "page-1")
    registry.complete(
        handle.generation_id,
        final_message_id="page-1",
        card_id="card-1",
        next_sequence=2,
    )

    retry = registry.claim_retry_token(
        handle.retry_token,
        actor_open_id="requester-1",
        chat_id="chat-1",
        current_session_id="session-1",
    )
    assert retry is not None
    assert retry.prompt == "normalized attachment content"


def test_failed_good_write_can_rearm_the_pending_intent() -> None:
    registry = ReplyActionRegistry()
    handle, _completion = _ready(registry)
    registry.observe_reaction(
        message_id="page-2",
        actor_open_id="requester-1",
        operator_type="user",
        emoji_type="THUMBSUP",
        action_time=1,
        created=True,
    )

    assert (
        registry.claim_pending_good(handle.generation_id, current_session_id="session-1")
        is not None
    )
    assert registry.release_good_claim(handle.generation_id) is True
    assert (
        registry.claim_pending_good(handle.generation_id, current_session_id="session-1")
        is not None
    )


def test_failed_good_write_preserves_a_reaction_on_another_answer_card() -> None:
    registry = ReplyActionRegistry()
    handle, _completion = _ready(registry)
    for message_id in ("page-1", "page-2"):
        registry.observe_reaction(
            message_id=message_id,
            actor_open_id="requester-1",
            operator_type="user",
            emoji_type="THUMBSUP",
            action_time=1,
            created=True,
        )
    assert (
        registry.claim_pending_good(handle.generation_id, current_session_id="session-1")
        is not None
    )

    assert (
        registry.observe_reaction(
            message_id="page-1",
            actor_open_id="requester-1",
            operator_type="user",
            emoji_type="THUMBSUP",
            action_time=2,
            created=False,
        )
        is ReactionTransition.EXECUTED
    )
    assert registry.release_good_claim(handle.generation_id) is True

    assert (
        registry.claim_pending_good(handle.generation_id, current_session_id="session-1")
        is not None
    )


def test_retry_claim_is_current_only_until_a_new_generation_begins() -> None:
    registry = ReplyActionRegistry()
    handle, _completion = _ready(registry)
    retry = registry.claim_retry_token(
        handle.retry_token,
        actor_open_id="requester-1",
        chat_id="chat-1",
        current_session_id="session-1",
    )
    assert retry is not None
    assert registry.is_current_retry_claim(handle.generation_id, "chat-1:requester-1")

    registry.begin(_inbound(message_id="new-source"), prompt="new", session_id="session-1")

    assert not registry.is_current_retry_claim(handle.generation_id, "chat-1:requester-1")


def test_exact_reaction_names_and_newer_timestamps_only() -> None:
    registry = ReplyActionRegistry()
    handle = registry.begin(_inbound(), prompt="normalized", session_id="session-1")
    registry.observe_message(handle.generation_id, "page-1")

    for ignored in ("CROSS", "EYE", "❌", "thumbsup"):
        assert (
            registry.observe_reaction(
                message_id="page-1",
                actor_open_id="requester-1",
                operator_type="user",
                emoji_type=ignored,
                action_time=1,
                created=True,
            )
            is ReactionTransition.IGNORED
        )

    assert (
        registry.observe_reaction(
            message_id="page-1",
            actor_open_id="requester-1",
            operator_type="user",
            emoji_type="CrossMark",
            action_time=10,
            created=True,
        )
        is ReactionTransition.PENDING
    )
    assert (
        registry.observe_reaction(
            message_id="page-1",
            actor_open_id="requester-1",
            operator_type="user",
            emoji_type="CrossMark",
            action_time=10,
            created=False,
        )
        is ReactionTransition.IGNORED
    )
    assert (
        registry.observe_reaction(
            message_id="page-1",
            actor_open_id="requester-1",
            operator_type="user",
            emoji_type="CrossMark",
            action_time=9,
            created=False,
        )
        is ReactionTransition.IGNORED
    )
    assert (
        registry.observe_reaction(
            message_id="page-1",
            actor_open_id="requester-1",
            operator_type="user",
            emoji_type="CrossMark",
            action_time=11,
            created=False,
        )
        is ReactionTransition.CANCELLED
    )
    assert (
        registry.observe_reaction(
            message_id="page-1",
            actor_open_id="requester-1",
            operator_type="user",
            emoji_type="CrossMark",
            action_time=12,
            created=True,
        )
        is ReactionTransition.PENDING
    )


def test_streaming_reactions_become_claimable_only_after_certain_completion() -> None:
    registry = ReplyActionRegistry()
    handle = registry.begin(_inbound(), prompt="normalized", session_id="session-1")
    registry.observe_message(handle.generation_id, "page-1")
    for emoji in ("THUMBSUP", "CrossMark"):
        assert (
            registry.observe_reaction(
                message_id="page-1",
                actor_open_id="requester-1",
                operator_type="user",
                emoji_type=emoji,
                action_time=1,
                created=True,
            )
            is ReactionTransition.PENDING
        )

    completion = registry.complete(
        handle.generation_id,
        final_message_id="page-1",
        card_id="card-1",
        next_sequence=2,
    )
    assert completion is not None
    assert completion.pending_actions == frozenset({ReactionAction.GOOD, ReactionAction.RETRY})

    good = registry.claim_pending_good(handle.generation_id, current_session_id="session-1")
    retry = registry.claim_pending_retry(handle.generation_id, current_session_id="session-1")
    assert good is not None and good.final_message_id == "page-1"
    assert retry is not None and retry.prompt == "normalized"
    assert registry.state(handle.generation_id) is ReplyActionState.RETRY_CLAIMED


def test_reaction_delete_after_claim_does_not_compensate() -> None:
    registry = ReplyActionRegistry()
    handle, _completion = _ready(registry)
    assert (
        registry.observe_reaction(
            message_id="page-1",
            actor_open_id="requester-1",
            operator_type="user",
            emoji_type="CrossMark",
            action_time=1,
            created=True,
        )
        is ReactionTransition.READY
    )
    assert (
        registry.claim_pending_retry(handle.generation_id, current_session_id="session-1")
        is not None
    )

    assert (
        registry.observe_reaction(
            message_id="page-1",
            actor_open_id="requester-1",
            operator_type="user",
            emoji_type="CrossMark",
            action_time=2,
            created=False,
        )
        is ReactionTransition.EXECUTED
    )
    assert registry.state(handle.generation_id) is ReplyActionState.RETRY_CLAIMED


def test_wrong_authority_does_not_consume_rightful_retry() -> None:
    registry = ReplyActionRegistry()
    handle, _completion = _ready(registry)

    for actor, chat, session in (
        ("wrong", "chat-1", "session-1"),
        ("requester-1", "wrong", "session-1"),
        ("requester-1", "chat-1", "wrong"),
    ):
        assert (
            registry.claim_retry_token(
                handle.retry_token,
                actor_open_id=actor,
                chat_id=chat,
                current_session_id=session,
            )
            is None
        )

    assert (
        registry.claim_retry_token(
            handle.retry_token,
            actor_open_id="requester-1",
            chat_id="chat-1",
            current_session_id="session-1",
        )
        is not None
    )


@pytest.mark.parametrize("_attempt", range(50))
def test_button_and_reaction_retry_race_has_one_winner(_attempt: int) -> None:
    registry = ReplyActionRegistry()
    handle, _completion = _ready(registry)
    assert (
        registry.observe_reaction(
            message_id="page-1",
            actor_open_id="requester-1",
            operator_type="user",
            emoji_type="CrossMark",
            action_time=1,
            created=True,
        )
        is ReactionTransition.READY
    )
    barrier = threading.Barrier(3)
    winners: list[str] = []

    def _button() -> None:
        barrier.wait()
        claimed = registry.claim_retry_token(
            handle.retry_token,
            actor_open_id="requester-1",
            chat_id="chat-1",
            current_session_id="session-1",
        )
        if claimed is not None:
            winners.append("button")

    def _reaction() -> None:
        barrier.wait()
        claimed = registry.claim_pending_retry(
            handle.generation_id,
            current_session_id="session-1",
        )
        if claimed is not None:
            winners.append("reaction")

    threads = [threading.Thread(target=_button), threading.Thread(target=_reaction)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert len(winners) == 1
    assert (
        registry.claim_retry_token(
            handle.retry_token,
            actor_open_id="requester-1",
            chat_id="chat-1",
            current_session_id="session-1",
        )
        is None
    )


def test_shutdown_clears_all_capsules_and_indexes() -> None:
    registry = ReplyActionRegistry()
    handle, _completion = _ready(registry)

    registry.close()

    assert len(registry) == 0
    assert registry.message_generation("page-1") is None
    assert registry.state(handle.generation_id) is None
    assert registry.begin(_inbound(), prompt="later", session_id="session-1") is None
