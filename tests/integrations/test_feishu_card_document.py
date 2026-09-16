"""The pure card layer: card JSON, byte accounting, and pagination."""

from __future__ import annotations

import pytest

from integrations.feishu.card_document import (
    CardPage,
    _blocks,
    paginate,
    render_card_spec,
    safe_prefix,
    spec_bytes,
    table_count,
)

_TABLE = "| a | b |\n| --- | --- |\n| 1 | 2 |"


def test_renders_the_schema_version_that_supports_tables() -> None:
    spec = render_card_spec("hello", streaming=True)
    assert spec["schema"] == "2.0"
    elements = spec["body"]["elements"]  # type: ignore[index]
    assert elements[0]["tag"] == "markdown"
    assert elements[0]["content"] == "hello"


def test_streaming_flag_reaches_the_config() -> None:
    assert render_card_spec("x", streaming=True)["config"]["streaming_mode"] is True  # type: ignore[index]
    assert render_card_spec("x", streaming=False)["config"]["streaming_mode"] is False  # type: ignore[index]


def test_bytes_are_utf8_not_character_count() -> None:
    """A CJK character is three bytes; measuring by len() would overfill the card."""
    spec = render_card_spec("中", streaming=False)
    assert spec_bytes(spec) == spec_bytes(render_card_spec("", streaming=False)) + 3


def test_short_text_is_one_page() -> None:
    pages = paginate("hello")
    assert pages == [CardPage(text="hello", index=1)]


def test_blank_text_is_no_pages() -> None:
    assert paginate("") == []
    assert paginate("   \n  ") == []


def test_pagination_loses_no_content() -> None:
    """The exit criterion, encoded directly."""
    text = "\n\n".join(f"paragraph {i} " + "x" * 300 for i in range(200))
    pages = paginate(text)
    assert len(pages) > 1
    assert "\n\n".join(page.text for page in pages) == text


def test_every_page_fits_the_budget() -> None:
    text = "\n\n".join("y" * 400 for _ in range(200))
    for page in paginate(text):
        assert spec_bytes(render_card_spec(page.text, streaming=False)) <= 28 * 1024


def test_a_fenced_code_block_is_never_split_across_pages() -> None:
    fence = "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(40)) + "\n```"
    text = ("z" * 3000 + "\n\n") * 20 + fence
    pages = paginate(text)
    joined = [page.text for page in pages]
    assert any(fence in page for page in joined)


def test_blocks_keeps_a_fenced_code_block_with_internal_blank_lines_whole() -> None:
    """Without fence tracking, a code block would be cut apart at its blank lines."""
    fence = "```python\n" + "\n\n".join(f"line_{i} = {i}" for i in range(40)) + "\n```"

    blocks = _blocks(f"intro\n\n{fence}\n\noutro")

    assert blocks == ["intro", fence, "outro"]


def test_table_count_is_a_second_budget_dimension() -> None:
    """Six small tables fit the byte budget but still overflow the card."""
    text = "\n\n".join(_TABLE for _ in range(6))
    pages = paginate(text)
    assert len(pages) >= 2


def test_table_count_counts_gfm_tables() -> None:
    assert table_count("\n\n".join(_TABLE for _ in range(6))) == 6
    assert table_count("just a paragraph, no pipes here") == 0


def test_a_table_is_never_split_across_pages() -> None:
    text = ("w" * 3000 + "\n\n") * 10 + _TABLE
    pages = paginate(text)
    assert any(_TABLE in page.text for page in pages)


def test_a_single_oversize_block_is_hard_split_and_still_loses_nothing() -> None:
    text = "q" * 200_000
    pages = paginate(text)
    assert len(pages) > 1
    assert "".join(page.text for page in pages) == text


def test_the_prefix_stops_at_a_block_boundary_rather_than_inside_a_fence() -> None:
    """A byte cut would land mid-fence; the remainder would then re-pair the fence."""
    fence = "```python\n" + "\n\n".join(f"line_{i} = {i}" for i in range(40)) + "\n```"
    text = "p" * 300 + "\n\n\n\n" + fence + "\n\n" + "z" * 300
    budget = spec_bytes(render_card_spec("", streaming=False)) + 300 + 100

    prefix = safe_prefix(text, budget=budget)

    assert text.startswith(prefix), "the prefix must be literal, not content-equivalent"
    assert prefix == "p" * 300, "the cut must back off to the blank line before the fence"
    assert fence in text[len(prefix) :], "the fence must survive whole in the remainder"


def test_a_first_block_too_large_for_the_budget_still_yields_a_prefix() -> None:
    """No block boundary exists inside one block, so the cut has to be byte-wise."""
    text = "q" * 5_000

    prefix = safe_prefix(text, budget=1_000)

    assert prefix, "an empty result would leave the caller no way to make progress"
    assert prefix != text
    assert text.startswith(prefix)
    assert spec_bytes(render_card_spec(prefix, streaming=False)) <= 1_000


def test_indices_are_one_based_and_contiguous() -> None:
    pages = paginate("\n\n".join("b" * 3000 for _ in range(30)))
    assert [page.index for page in pages] == list(range(1, len(pages) + 1))


def test_a_budget_that_cannot_hold_a_card_is_rejected() -> None:
    """Below the empty-card overhead the splitter cannot make progress."""
    with pytest.raises(ValueError, match="cannot hold"):
        paginate("abcdef", budget=10)
