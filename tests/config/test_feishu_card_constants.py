"""The card constants are shared, so they must travel through the facade."""

from __future__ import annotations

from config.constants import (
    FEISHU_CARD_BUDGET_BYTES,
    FEISHU_CARD_MAX_BYTES,
    FEISHU_CARD_MAX_TABLES,
    FEISHU_CARD_SCHEMA,
    FEISHU_CARD_TRUNCATED_MARKER,
    FEISHU_STREAM_ELEMENT_ID,
    FEISHU_STREAM_ERROR_CODES,
)


def test_budget_leaves_headroom_under_the_hard_cap() -> None:
    assert FEISHU_CARD_BUDGET_BYTES < FEISHU_CARD_MAX_BYTES


def test_schema_is_the_version_that_renders_tables() -> None:
    assert FEISHU_CARD_SCHEMA == "2.0"


def test_element_id_satisfies_the_component_constraint() -> None:
    # CardKit: letter first, alphanumeric/underscore only, at most 20 chars.
    assert FEISHU_STREAM_ELEMENT_ID[0].isalpha()
    assert FEISHU_STREAM_ELEMENT_ID.isalnum() or "_" in FEISHU_STREAM_ELEMENT_ID
    assert len(FEISHU_STREAM_ELEMENT_ID) <= 20


def test_table_cap_matches_the_platform_limit() -> None:
    assert FEISHU_CARD_MAX_TABLES == 5


def test_stream_error_codes_are_the_three_documented_ones() -> None:
    assert frozenset({200850, 300309, 300317}) == FEISHU_STREAM_ERROR_CODES


def test_truncation_marker_is_not_a_bare_ellipsis() -> None:
    # A bare "…" reads as "the answer ended here"; the marker must say otherwise.
    assert len(FEISHU_CARD_TRUNCATED_MARKER.strip()) > 3
