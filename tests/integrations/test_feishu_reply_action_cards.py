"""Reply actions expose only opaque adoption and retry identifiers."""

import json

import pytest

from integrations.feishu.reply_action_cards import render_reply_action_elements


def test_reply_actions_share_one_two_button_row() -> None:
    elements = render_reply_action_elements(
        feedback_token="feedback-token",
        retry_token="retry-token",
    )

    assert len(elements) == 2
    assert [element["text"] for element in elements] == [
        {"tag": "plain_text", "content": "✅ 采纳"},
        {"tag": "plain_text", "content": "🔄 重试"},
    ]
    assert [element["behaviors"] for element in elements] == [
        [{"type": "callback", "value": {"feedback_id": "feedback-token"}}],
        [{"type": "callback", "value": {"retry_id": "retry-token"}}],
    ]


def test_reply_actions_serialize_no_authority_or_content() -> None:
    serialized = json.dumps(
        render_reply_action_elements(
            feedback_token="feedback-token",
            retry_token="retry-token",
        )
    )

    for forbidden in (
        "actor",
        "open_id",
        "chat_id",
        "message_id",
        "card_id",
        "session_id",
        "prompt",
        "answer",
        "attachment",
        "verdict",
        "approval_id",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("feedback_token", "retry_token"),
    [("", "retry"), (" ", "retry"), ("feedback", ""), ("feedback", " "), ("same", "same")],
)
def test_reply_actions_reject_unusable_or_equal_tokens(
    feedback_token: str,
    retry_token: str,
) -> None:
    with pytest.raises(ValueError):
        render_reply_action_elements(
            feedback_token=feedback_token,
            retry_token=retry_token,
        )
