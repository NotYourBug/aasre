"""Approval and feedback payloads cannot cross-route or mix authority."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from gateway.transports.feishu.card_actions import handle_card_action


def test_exact_router_rejects_mixed_and_ignores_unknown() -> None:
    feedback = MagicMock()
    broker = MagicMock()
    pending = MagicMock()
    for value in (
        {"approval_id": "a", "feedback_id": "f"},
        {"feedback_id": "f", "extra": "x"},
        {"unknown": "x"},
    ):
        data = SimpleNamespace(event=SimpleNamespace(action=SimpleNamespace(value=value)))
        response = handle_card_action(
            data,
            broker=broker,
            pending_approvals=pending,
            env_allowed_open_ids=[],
            logger=logging.getLogger(__name__),
            feedback=feedback,
        )
        assert response.card is None
    assert not feedback.mock_calls
    assert not broker.mock_calls
    assert not pending.mock_calls


def test_feedback_routes_without_approval_calls() -> None:
    feedback, broker, pending = MagicMock(), MagicMock(), MagicMock()
    data = SimpleNamespace(
        event=SimpleNamespace(action=SimpleNamespace(value={"feedback_id": "f"}))
    )
    handle_card_action(
        data,
        broker=broker,
        pending_approvals=pending,
        env_allowed_open_ids=[],
        logger=logging.getLogger(__name__),
        feedback=feedback,
    )
    feedback.handle_action.assert_called_once_with(data)
    assert not broker.mock_calls
