"""Which inbound Feishu messages earn a turn, and what the agent is told.

The load-bearing case is the S2 exit criterion — a screenshot with no caption
must reach the agent — because two separate early returns used to drop it.
"""

from __future__ import annotations

import json
from typing import Any

from gateway.transports.feishu.worker import build_inbound_message
from integrations.feishu import ResourceRef

SCREENSHOT_POST = {
    "title": "",
    "content": [
        [
            {"tag": "at", "user_id": "@_user_1", "user_name": "aasre", "style": []},
            {"tag": "text", "text": " ", "style": []},
        ],
        [{"tag": "img", "image_key": "img_v3_0215h_c80616f8", "width": 1247, "height": 984}],
    ],
}

CAPTIONED_POST = {
    "title": "",
    "content": [
        [
            {"tag": "at", "user_id": "@_user_1", "user_name": "aasre", "style": []},
            {"tag": "text", "text": " what broke?", "style": []},
        ],
        [{"tag": "img", "image_key": "img_v3_cb6cca06", "width": 1220, "height": 1083}],
    ],
}


class _SenderId:
    def __init__(self, open_id: str) -> None:
        self.open_id = open_id


class _Sender:
    def __init__(self, open_id: str, sender_type: str = "user") -> None:
        self.sender_type = sender_type
        self.sender_id = _SenderId(open_id)


class _Mention:
    def __init__(self, key: str, mentioned_type: str = "bot") -> None:
        self.key = key
        self.mentioned_type = mentioned_type


class _Message:
    def __init__(
        self,
        message_type: str,
        content: dict[str, Any],
        *,
        mentions: tuple[_Mention, ...] = (),
    ) -> None:
        self.message_type = message_type
        self.content = json.dumps(content)
        self.chat_id = "oc_chat-1"
        self.message_id = "om_1"
        self.parent_id = ""
        self.mentions = list(mentions)


def _user() -> _Sender:
    return _Sender("ou_user-1")


def _bot_mention() -> _Mention:
    return _Mention("@_user_1")


def test_a_screenshot_with_no_caption_still_starts_a_turn() -> None:
    """The S2 exit criterion: 'send a screenshot' must reach the agent."""
    inbound = build_inbound_message(
        _user(), _Message("post", SCREENSHOT_POST, mentions=(_bot_mention(),))
    )

    assert inbound is not None
    assert inbound.text == ""
    assert inbound.attachments == (ResourceRef(kind="image", key="img_v3_0215h_c80616f8"),)


def test_a_post_contributes_both_its_text_and_its_image() -> None:
    """A group screenshot arrives as post; dropping either half loses content."""
    inbound = build_inbound_message(
        _user(), _Message("post", CAPTIONED_POST, mentions=(_bot_mention(),))
    )

    assert inbound is not None
    assert "what broke?" in inbound.text
    assert inbound.attachments == (ResourceRef(kind="image", key="img_v3_cb6cca06"),)


def test_a_file_message_is_tracked_under_its_filename() -> None:
    inbound = build_inbound_message(
        _user(), _Message("file", {"file_key": "file_v3_x", "file_name": "app.log"})
    )

    assert inbound is not None
    assert inbound.attachments == (ResourceRef(kind="file", key="file_v3_x", name="app.log"),)


def test_a_sticker_starts_no_turn_at_all() -> None:
    """Not merely unanswered — no session, no agent call, and no reply."""
    assert build_inbound_message(_user(), _Message("sticker", {"file_key": "st-1"})) is None


def test_a_post_with_nothing_readable_starts_no_turn() -> None:
    """Neither text nor a resource means there is nothing to hand the agent."""
    assert (
        build_inbound_message(_user(), _Message("post", {"content": [[{"tag": "nope"}]]})) is None
    )


def test_a_message_without_a_chat_or_sender_starts_no_turn() -> None:
    message = _Message("file", {"file_key": "f1", "file_name": "a.log"})
    message.chat_id = ""
    assert build_inbound_message(_user(), message) is None
    assert build_inbound_message(_Sender(""), _Message("text", {"text": "hi"})) is None


def test_plain_text_is_unchanged() -> None:
    """text, mentions-stripped, no attachment section appended."""
    inbound = build_inbound_message(
        _user(), _Message("text", {"text": "@_user_1 /status"}, mentions=(_bot_mention(),))
    )

    assert inbound is not None
    assert inbound.text == "/status"
    assert inbound.attachments == ()
