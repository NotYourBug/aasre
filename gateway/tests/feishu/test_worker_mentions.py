"""Feishu worker mention handling: strip only the bot's own @-mention key."""

from __future__ import annotations

import pytest

from gateway.transports.feishu.worker import strip_leading_feishu_mentions

BOT_KEY = frozenset({"@_user_1"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@_user_1 /stop", "/stop"),
        # Only the bot's own key is stripped; another member's key stays.
        ("@_user_1 @_user_2 /new", "@_user_2 /new"),
        ("@_user_1 hello there", "hello there"),
        ("hello", "hello"),
        # A literal @all / @everyone / @name is not the bot's mention key and survives.
        ("@all hands on deck", "@all hands on deck"),
        ("@deploy now", "@deploy now"),
        ("", ""),
    ],
)
def test_strip_leading_feishu_mentions(raw: str, expected: str) -> None:
    assert strip_leading_feishu_mentions(raw, BOT_KEY) == expected


def test_no_bot_mention_key_leaves_text_intact() -> None:
    assert strip_leading_feishu_mentions("@_user_1 /stop", frozenset()) == "@_user_1 /stop"
