"""Final-card feedback authority and non-blocking callback receipts."""

import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.transports.feishu.card_stream import FinalCardTarget
from gateway.transports.feishu.feedback import FeishuFeedbackService
from gateway.transports.feishu.feedback_authority import FeedbackAuthorityStore
from integrations.feishu import FeishuCardCallError
from integrations.feishu.delivery_types import FeishuCardCallStage


def _callback(
    token: str, *, actor: str = "actor", chat: str = "chat", extra: bool = False
) -> SimpleNamespace:
    value = {"feedback_id": token}
    if extra:
        value["message_id"] = "injected"
    return SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(tag="button", value=value),
            operator=SimpleNamespace(open_id=actor),
            context=SimpleNamespace(open_chat_id=chat),
        )
    )


def test_callback_authority_idempotency_and_payload(tmp_path: Path) -> None:
    authority = FeedbackAuthorityStore(tmp_path / "authority.jsonl")
    authority.register(token="token", requester_open_id="actor", chat_id="chat", message_id="final")
    client = MagicMock()
    path = tmp_path / "feedback.jsonl"
    service = FeishuFeedbackService(
        authority=authority,
        feedback_path=path,
        card_client=client,
        authorized=lambda actor, _chat: actor in {"actor", "bystander"},
    )
    for callback in (
        _callback("token", actor="bystander"),
        _callback("token", chat="wrong"),
        _callback("unknown"),
        _callback("token", extra=True),
        _callback("token", actor="denied"),
    ):
        service.handle_action(callback)
    service.shutdown(timeout_seconds=5)
    assert not path.exists()
    service = FeishuFeedbackService(
        authority=authority,
        feedback_path=path,
        card_client=client,
        authorized=lambda _actor, _chat: True,
    )
    for _ in range(10):
        response = service.handle_action(_callback("token"))
        assert response.card is None
    service.shutdown(timeout_seconds=5)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["message_id"] == "final"
    assert rows[0]["verdict"] == "good"
    assert set(rows[0]) == {"ts", "platform", "user_id", "chat_id", "message_id", "verdict"}
    client.assert_not_called()


def test_blocked_authority_does_not_block_callback_and_queue_is_bounded(tmp_path: Path) -> None:
    entered, release = Event(), Event()
    authority = MagicMock()

    def find(token: str) -> None:
        assert token == "token"
        entered.set()
        assert release.wait(5)

    authority.find.side_effect = find
    service = FeishuFeedbackService(
        authority=authority,
        feedback_path=tmp_path / "feedback.jsonl",
        card_client=MagicMock(),
        authorized=lambda _actor, _chat: True,
        queue_limit=1,
    )
    try:
        response = service.handle_action(_callback("token"))
        assert response.toast.content == "已收到"
        assert entered.wait(5)
        assert service.handle_action(_callback("token")).toast.content != "已收到"
    finally:
        release.set()
        service.shutdown(timeout_seconds=5)


def test_authority_precedes_button_visibility(tmp_path: Path) -> None:
    authority = FeedbackAuthorityStore(tmp_path / "authority.jsonl")
    client = MagicMock()
    appended = Event()

    def append(
        card_id: str, elements: list[dict[str, object]], sequence: int, *, uuid: str
    ) -> None:
        assert (card_id, sequence) == ("card", 7)
        assert uuid
        token = elements[0]["behaviors"][0]["value"]["feedback_id"]
        assert authority.find(token).message_id == "final"
        appended.set()

    client.append_elements.side_effect = append
    service = FeishuFeedbackService(
        authority=authority,
        feedback_path=tmp_path / "feedback.jsonl",
        card_client=client,
        authorized=lambda _actor, _chat: True,
    )
    service.issue(FinalCardTarget("card", "final", 7), requester_open_id="actor", chat_id="chat")
    assert appended.wait(5)
    service.shutdown(timeout_seconds=5)
    assert client.append_elements.call_count == 1


@pytest.mark.parametrize("definite", [True, False])
def test_append_failure_preserves_only_uncertain_authority(tmp_path: Path, definite: bool) -> None:
    authority = FeedbackAuthorityStore(tmp_path / "authority.jsonl")
    client = MagicMock()
    tokens: list[str] = []

    def append(
        _card_id: str, elements: list[dict[str, object]], _sequence: int, *, uuid: str
    ) -> None:
        assert uuid
        payload = json.loads(json.dumps(elements))
        tokens.append(payload[0]["behaviors"][0]["value"]["feedback_id"])
        if definite:
            raise FeishuCardCallError(code=230002, stage=FeishuCardCallStage.APPEND_ELEMENTS)
        raise TimeoutError("sensitive vendor detail")

    client.append_elements.side_effect = append
    service = FeishuFeedbackService(
        authority=authority,
        feedback_path=tmp_path / "feedback.jsonl",
        card_client=client,
        authorized=lambda _actor, _chat: True,
    )
    service.issue(FinalCardTarget("card", "final", 7), requester_open_id="actor", chat_id="chat")
    service.shutdown(timeout_seconds=5)
    assert len(tokens) == 1
    assert (authority.find(tokens[0]) is None) is definite
    assert client.append_elements.call_count == 1
