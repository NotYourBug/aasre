"""Feishu worker mention handling: strip leading @-tokens from text."""

from __future__ import annotations

import pytest

from gateway.transports.feishu.worker import strip_leading_feishu_mentions


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@_user_1 /stop", "/stop"),
        ("@_user_1 @_user_2 /new", "/new"),
        ("@_user_1 hello there", "hello there"),
        ("hello", "hello"),
        ("@all hands on deck", "hands on deck"),
        ("", ""),
    ],
)
def test_strip_leading_feishu_mentions(raw: str, expected: str) -> None:
    assert strip_leading_feishu_mentions(raw) == expected
