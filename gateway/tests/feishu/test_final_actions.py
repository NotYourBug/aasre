"""Final Feishu adoption/retry action issuance."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock

import pytest

from gateway.transports.feishu.card_stream import FinalCardTarget
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.feedback import FeishuFeedbackService
from gateway.transports.feishu.feedback_authority import FeedbackAuthorityStore
from gateway.transports.feishu.final_actions import FinalActionCoordinator
from gateway.transports.feishu.reply_actions import ReplyActionRegistry, ReplyActionState
from integrations.feishu import FeishuCardCallError
from integrations.feishu.delivery_types import FeishuCardCallStage


def _services(tmp_path: Path, *, client: MagicMock | None = None):
    authority = FeedbackAuthorityStore(tmp_path / "authority.jsonl")
    feedback = FeishuFeedbackService(
        authority=authority,
        feedback_path=tmp_path / "feedback.jsonl",
        authorized=lambda _actor, _chat: True,
    )
    registry = ReplyActionRegistry()
    coordinator = FinalActionCoordinator(
        feedback=feedback,
        reply_actions=registry,
        card_client=client or MagicMock(),
    )
    return authority, feedback, registry, coordinator


def _generation(registry: ReplyActionRegistry):
    handle = registry.begin(
        FeishuInboundMessage("chat", "actor", "source", "prompt"),
        prompt="normalized prompt",
        session_id="session",
    )
    assert handle is not None
    registry.observe_message(handle.generation_id, "final")
    return handle


def test_authority_is_durable_before_one_combined_append(tmp_path: Path) -> None:
    client = MagicMock()
    authority, feedback, registry, coordinator = _services(tmp_path, client=client)
    handle = _generation(registry)
    appended = Event()

    def append(
        card_id: str, elements: list[dict[str, object]], sequence: int, *, uuid: str
    ) -> None:
        assert (card_id, sequence) == ("card", 7)
        assert uuid
        assert [element["text"]["content"] for element in elements] == ["✅ 采纳", "🔄 重试"]
        feedback_token = elements[0]["behaviors"][0]["value"]["feedback_id"]
        retry_token = elements[1]["behaviors"][0]["value"]["retry_id"]
        assert authority.find(feedback_token).message_id == "final"
        assert retry_token == handle.retry_token
        appended.set()

    client.append_elements.side_effect = append
    assert coordinator.issue(
        handle,
        FinalCardTarget("card", "final", 7),
        requester_open_id="actor",
        chat_id="chat",
    )
    assert appended.wait(5)
    coordinator.shutdown(timeout_seconds=5)
    feedback.shutdown(timeout_seconds=5)
    assert client.append_elements.call_count == 1
    assert registry.state(handle.generation_id) is ReplyActionState.READY


@pytest.mark.parametrize("definite", [True, False])
def test_append_failure_invalidates_only_definite_adoption_authority(
    tmp_path: Path, definite: bool
) -> None:
    client = MagicMock()
    authority, feedback, registry, coordinator = _services(tmp_path, client=client)
    handle = _generation(registry)
    tokens: list[str] = []

    def append(
        _card_id: str, elements: list[dict[str, object]], _sequence: int, *, uuid: str
    ) -> None:
        assert uuid
        payload = json.loads(json.dumps(elements))
        tokens.append(payload[0]["behaviors"][0]["value"]["feedback_id"])
        if definite:
            raise FeishuCardCallError(code=230002, stage=FeishuCardCallStage.APPEND_ELEMENTS)
        raise TimeoutError("vendor detail")

    client.append_elements.side_effect = append
    coordinator.issue(
        handle,
        FinalCardTarget("card", "final", 7),
        requester_open_id="actor",
        chat_id="chat",
    )
    coordinator.shutdown(timeout_seconds=5)
    feedback.shutdown(timeout_seconds=5)

    assert len(tokens) == 1
    assert (authority.find(tokens[0]) is None) is definite
    assert registry.state(handle.generation_id) is ReplyActionState.READY
