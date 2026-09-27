"""Final-card feedback authority and non-blocking callback receipts."""

import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

from gateway.transports.feishu.feedback import FeishuFeedbackService
from gateway.transports.feishu.feedback_authority import FeedbackAuthorityStore


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
    path = tmp_path / "feedback.jsonl"
    service = FeishuFeedbackService(
        authority=authority,
        feedback_path=path,
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
