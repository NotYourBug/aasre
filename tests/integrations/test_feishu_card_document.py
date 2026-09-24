"""The pure card layer: card JSON, byte accounting, and pagination."""

from __future__ import annotations

from itertools import pairwise

import pytest

from config.constants import FEISHU_CARD_BUDGET_BYTES
from integrations.feishu.card_document import (
    CardPage,
    _blocks,
    _is_table,
    paginate,
    render_card_spec,
    safe_prefix,
    spec_bytes,
    table_count,
)

_EMPTY_CARD_BYTES = spec_bytes(render_card_spec("", streaming=False))


def _budget(headroom: int) -> int:
    """A budget of one empty card plus *headroom* bytes of content."""
    return _EMPTY_CARD_BYTES + headroom


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


def test_short_text_has_the_whole_source_range() -> None:
    assert paginate("hello") == [CardPage(text="hello", index=1, source_start=0, source_end=5)]


def test_blank_text_is_no_pages() -> None:
    assert paginate("") == []
    assert paginate("   \n  ") == []


def test_pagination_loses_no_content() -> None:
    """The exit criterion, encoded directly."""
    text = "\n\n".join(f"paragraph {i} " + "x" * 300 for i in range(600))
    pages = paginate(text)
    assert len(pages) > 1
    assert "".join(page.text for page in pages) == text


def test_pagination_preserves_whitespace_runs_at_page_boundaries() -> None:
    first = "a" * 500
    text = first + "\n\n\n\n" + "b" * 500

    pages = paginate(text, budget=_budget(550))

    assert len(pages) == 2
    assert "".join(page.text for page in pages) == text


def test_a_large_leading_whitespace_run_still_respects_every_page_budget() -> None:
    budget = _budget(100)
    text = "\n" * 500 + "body"

    pages = paginate(text, budget=budget)

    assert "".join(page.text for page in pages) == text
    assert all(spec_bytes(render_card_spec(page.text, streaming=False)) <= budget for page in pages)


def test_every_page_fits_the_budget() -> None:
    text = "\n\n".join("y" * 400 for _ in range(400))
    for page in paginate(text):
        spec = render_card_spec(page.text, streaming=False)
        assert spec_bytes(spec) <= FEISHU_CARD_BUDGET_BYTES
        # The streaming element's ceiling is characters, not bytes.
        assert len(page.text) <= 100_000


def test_a_fenced_code_block_is_never_split_across_pages() -> None:
    fence = "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(40)) + "\n```"
    text = ("z" * 3000 + "\n\n") * 20 + fence

    pages = paginate(text, budget=_budget(4_000))

    assert len(pages) > 1, "a single page would make the assertion below vacuous"
    assert any(fence in page.text for page in pages), "the fence must survive whole"


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

    pages = paginate(text, budget=_budget(4_000))

    assert len(pages) > 1, "a single page would make the assertion below vacuous"
    assert any(_TABLE in page.text for page in pages), "the table must survive whole"


_CODE_LINES = [f"line_{i} = {i}" for i in range(600)]
_OVERSIZE_FENCE = "```python\n" + "\n".join(_CODE_LINES) + "\n```"
_TABLE_ROWS = [f"| {i} | {i * 2} |" for i in range(400)]
_OVERSIZE_TABLE = "\n".join(["| a | b |", "| --- | --- |", *_TABLE_ROWS])


@pytest.mark.parametrize(
    "text",
    [
        "first\r\n\r\nsecond\r\nthird",
        "标题\u2028段落\n\n尾部🙂",
        _OVERSIZE_FENCE,
        _OVERSIZE_TABLE,
    ],
)
def test_source_ranges_partition_the_original_text(text: str) -> None:
    pages = paginate(text, budget=_budget(1_000))

    assert pages[0].source_start == 0
    assert pages[-1].source_end == len(text)
    assert all(left.source_end == right.source_start for left, right in pairwise(pages))
    assert "".join(text[page.source_start : page.source_end] for page in pages) == text


def test_an_oversize_code_block_closes_and_reopens_its_fence_on_every_card() -> None:
    """A byte-wise cut would leave page 1 open and the rest rendering as prose."""
    pages = paginate(_OVERSIZE_FENCE, budget=_budget(1_000))

    assert len(pages) > 1
    for page in pages:
        lines = page.text.splitlines()
        assert lines[0] == "```python", "every card must reopen the fence"
        assert lines[-1] == "```", "every card must close its own fence"
        assert spec_bytes(render_card_spec(page.text, streaming=False)) <= _budget(1_000)
    carried = [line for page in pages for line in page.text.splitlines()[1:-1]]
    assert carried == _CODE_LINES, "the code must survive once each, in order"


def test_an_oversize_tilde_fence_is_reopened_with_its_original_delimiter() -> None:
    code = [f"line_{i} = {i}" for i in range(600)]
    text = "~~~~python\n" + "\n".join(code) + "\n~~~~"

    pages = paginate(text, budget=_budget(1_000))

    assert len(pages) > 1
    for page in pages:
        lines = page.text.splitlines()
        assert lines[0] == "~~~~python"
        assert lines[-1] == "~~~~"
    assert [line for page in pages for line in page.text.splitlines()[1:-1]] == code


def test_an_oversize_table_repeats_its_header_and_delimiter_on_every_card() -> None:
    """A continuation without the delimiter row is not a table at all."""
    pages = paginate(_OVERSIZE_TABLE, budget=_budget(1_000))

    assert len(pages) > 1
    for page in pages:
        lines = page.text.splitlines()
        assert lines[0] == "| a | b |", "every card must repeat the header"
        assert lines[1] == "| --- | --- |", "every card must repeat the delimiter"
        assert _is_table(page.text)
    carried = [line for page in pages for line in page.text.splitlines()[2:]]
    assert carried == _TABLE_ROWS, "the rows must survive once each, in order"


def test_a_single_oversize_block_is_hard_split_and_still_loses_nothing() -> None:
    text = "q" * 200_000
    pages = paginate(text)
    assert len(pages) > 1
    assert "".join(page.text for page in pages) == text


def test_separator_before_an_oversize_block_does_not_become_a_blank_card() -> None:
    text = "intro\n\n" + ("x" * 70_000)

    pages = paginate(text)

    assert all(page.text.strip() for page in pages)
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


def test_a_budget_that_holds_the_envelope_but_no_content_is_rejected() -> None:
    """The empty card fits, one character does not — no page may exceed the budget."""
    with pytest.raises(ValueError, match="cannot hold"):
        paginate("abcdef", budget=_EMPTY_CARD_BYTES)


def test_a_structured_first_block_too_large_for_the_budget_yields_no_prefix() -> None:
    """A literal cut inside a fence leaves the remainder with an unpaired one.

    Nothing here is lost: an empty prefix routes the caller to `paginate`, which
    reopens the fence on each card.
    """
    assert safe_prefix(_OVERSIZE_FENCE, budget=_budget(1_000)) == ""
    assert safe_prefix(_OVERSIZE_TABLE, budget=_budget(1_000)) == ""
