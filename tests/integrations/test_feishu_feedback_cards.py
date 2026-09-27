"""Feedback components expose only an opaque action identifier."""

import json

import pytest

from integrations.feishu import render_feedback_button_elements


def test_feedback_component_has_one_opaque_callback() -> None:
    elements = render_feedback_button_elements("opaque-token")
    assert len(elements) == 1
    button = elements[0]
    assert button["text"] == {"tag": "plain_text", "content": "✅ 采纳"}
    assert button["behaviors"] == [{"type": "callback", "value": {"feedback_id": "opaque-token"}}]
    serialized = json.dumps(elements)
    for forbidden in ("message_id", "chat_id", "open_id", "approval_id", "verdict"):
        assert forbidden not in serialized


def test_blank_token_cannot_create_an_unusable_button() -> None:
    with pytest.raises(ValueError):
        render_feedback_button_elements(" ")
