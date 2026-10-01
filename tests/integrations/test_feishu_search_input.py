"""Reject model-controlled search windows before any history access."""

from typing import Any

import pytest

from integrations.feishu.search_input import normalize_search_input
from integrations.feishu.search_types import (
    FeishuSearchError,
    FeishuSearchErrorCode,
    FeishuSearchInput,
)


def test_search_input_normalizes_query_and_accepts_exact_window_boundaries() -> None:
    assert normalize_search_input(
        start_time=1000, end_time=2000, query=" 故障 ", now=2000
    ) == FeishuSearchInput("故障", 1000, 2000, 10)
    assert normalize_search_input(
        start_time=0, end_time=604800, limit=20, now=604800
    ) == FeishuSearchInput("", 0, 604800, 20)


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_time": True},
        {"start_time": 1.0},
        {"start_time": "1000"},
        {"start_time": -1},
        {"start_time": 2000},
        {"end_time": 2001},
        {"start_time": 0, "end_time": 604801, "now": 604801},
        {"limit": 0},
        {"limit": 21},
        {"limit": True},
        {"limit": "10"},
    ],
)
def test_search_input_rejects_non_integer_and_unbounded_windows(overrides: dict[str, Any]) -> None:
    supplied: dict[str, Any] = {"start_time": 1000, "end_time": 2000, "now": 2000, **overrides}
    with pytest.raises(FeishuSearchError) as caught:
        normalize_search_input(**supplied)
    assert caught.value.code == FeishuSearchErrorCode.VALIDATION
    assert str(caught.value) == "validation"


@pytest.mark.parametrize("query", [None, 4, "\n故障", "故障\x7f", "故障\x85", "\ud800", "x" * 257])
def test_query_rejects_control_characters_before_trimming(query: object) -> None:
    with pytest.raises(FeishuSearchError) as caught:
        normalize_search_input(start_time=1000, end_time=2000, query=query, now=2000)
    assert caught.value.code == FeishuSearchErrorCode.VALIDATION
