"""Pure CardKit card construction for Feishu.

Turns markdown into ``schema 2.0`` card JSON and splits a document across as many
cards as its byte budget and table count require. No lark import and no network,
so both the streaming session and the one-way delivery paths build on it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from config.constants import (
    FEISHU_CARD_BUDGET_BYTES,
    FEISHU_CARD_MAX_TABLES,
    FEISHU_CARD_SCHEMA,
    FEISHU_STREAM_ELEMENT_ID,
)

_FENCE_OPEN = re.compile(r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})(?P<info>.*)$")

#: A GFM delimiter row — pipes, dashes, optional alignment colons.
_DELIMITER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")


@dataclass(frozen=True)
class CardPage:
    """One rendered card and its owned half-open original-source range."""

    text: str
    index: int
    source_start: int
    source_end: int


def render_card_spec(
    text: str, *, streaming: bool, element_id: str = FEISHU_STREAM_ELEMENT_ID
) -> dict[str, object]:
    """Return the ``schema 2.0`` card JSON holding a single markdown element."""
    return {
        "schema": FEISHU_CARD_SCHEMA,
        "config": {"streaming_mode": streaming, "summary": {"content": ""}},
        "body": {"elements": [{"tag": "markdown", "element_id": element_id, "content": text}]},
    }


def spec_bytes(spec: dict[str, object]) -> int:
    """Return the UTF-8 size of the serialized card.

    The platform cap is a byte count, and JSON escaping inflates the payload, so
    measuring the raw text would understate the card that actually gets sent.
    """
    return len(json.dumps(spec, ensure_ascii=False).encode("utf-8"))


@dataclass(frozen=True)
class _Structure:
    """A block that can be repeated across cards without breaking its markdown.

    ``head`` opens every card and ``tail`` closes it; ``rows`` carries the body.
    A fenced code block is ``(opening fence, code lines, closing fence)``, a GFM
    table is ``(header + delimiter, data rows, nothing)``.
    """

    head: tuple[str, ...]
    rows: tuple[str, ...]
    tail: tuple[str, ...]

    def card(self, rows: tuple[str, ...]) -> str:
        """Return one card's text, holding *rows* inside the structure."""
        return "\n".join((*self.head, *rows, *self.tail))


@dataclass(frozen=True)
class _BlockSpan:
    """One semantic markdown block and its literal offsets in the source."""

    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _RenderedSpan:
    """Rendered card text paired with the original source range it owns."""

    text: str
    source_start: int
    source_end: int


def _opening_fence(line: str) -> tuple[str, int] | None:
    """Return the fence character and width for a valid opening fence."""
    match = _FENCE_OPEN.match(line)
    if match is None:
        return None
    marker = match.group("marker")
    if marker[0] == "`" and "`" in match.group("info"):
        return None
    return marker[0], len(marker)


def _closes_fence(line: str, fence: tuple[str, int]) -> bool:
    """Return whether *line* closes *fence* under the GFM fence rules."""
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return False
    marker = stripped.rstrip(" ")
    char, width = fence
    return len(marker) >= width and set(marker) == {char}


def _fence_structure(lines: list[str]) -> _Structure | None:
    """Return the fenced-code view of *lines*, or ``None`` when it is no fence."""
    if not lines:
        return None
    fence = _opening_fence(lines[0])
    if fence is None:
        return None
    rows = lines[1:]
    closing = fence[0] * fence[1]
    if rows and _closes_fence(rows[-1], fence):
        closing = rows[-1]
        rows = rows[:-1]
    return _Structure(head=(lines[0],), rows=tuple(rows), tail=(closing,))


def _table_structure(lines: list[str]) -> _Structure | None:
    """Return the GFM-table view of *lines*, or ``None`` when it is no table."""
    if len(lines) < 2 or _DELIMITER.match(lines[1]) is None:
        return None
    return _Structure(head=(lines[0], lines[1]), rows=tuple(lines[2:]), tail=())


def _structure(block: str) -> _Structure | None:
    """Return the repeatable view of *block*, or ``None`` when it has none."""
    lines = block.splitlines()
    fence = _fence_structure(lines)
    return fence if fence is not None else _table_structure(lines)


def _is_table(block: str) -> bool:
    """Return whether *block* is a GFM table."""
    return _table_structure(block.splitlines()) is not None


#: Every terminator ``str.splitlines`` splits on. ``splitlines(keepends=True)``
#: is the only way to keep offsets into the original text, so each raw line is
#: stripped of exactly what the splitter left on it.
_LINE_TERMINATORS = "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029"


def _block_spans(text: str) -> list[_BlockSpan]:
    """Return semantic blocks with literal source offsets, in order.

    Separating whitespace is deliberately absent from the block text but remains
    recoverable from adjacent offsets, so pagination can preserve it byte-for-byte.
    """
    spans: list[_BlockSpan] = []
    start: int | None = None
    fence: tuple[str, int] | None = None
    offset = 0
    end = 0
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip(_LINE_TERMINATORS)
        line_start, offset = offset, offset + len(raw)
        if fence is None and not line.strip():
            if start is not None:
                spans.append(_BlockSpan(start=start, end=end, text=text[start:end]))
                start = None
            continue
        if start is None:
            start = line_start
        if fence is None:
            fence = _opening_fence(line)
        elif _closes_fence(line, fence):
            fence = None
        end = line_start + len(line)
    if start is not None:
        spans.append(_BlockSpan(start=start, end=end, text=text[start:end]))
    return spans


def _blocks(text: str) -> list[str]:
    """Split markdown into units that must not break across cards.

    A fenced code block is one unit even when it contains blank lines; outside a
    fence, a blank line ends the unit.
    """
    return [span.text for span in _block_spans(text)]


def table_count(text: str) -> int:
    """Return the number of GFM tables in *text*."""
    return sum(1 for block in _blocks(text) if _is_table(block))


def _fits(text: str, budget: int, *, streaming: bool = False) -> bool:
    return spec_bytes(render_card_spec(text, streaming=streaming)) <= budget


def _largest_prefix(text: str, budget: int, *, suffix: str = "", streaming: bool = False) -> int:
    """Return the longest character count whose card fits, or 0 when none does."""
    low, high = 1, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _fits(text[:mid] + suffix, budget, streaming=streaming):
            low = mid
        else:
            high = mid - 1
    return low if _fits(text[:low] + suffix, budget, streaming=streaming) else 0


def _largest_row_run(structure: _Structure, start: int, budget: int) -> int:
    """Return the largest exclusive end whose rows still fit one card."""
    low, high = start, len(structure.rows)
    while low < high:
        mid = (low + high + 1) // 2
        if _fits(structure.card(structure.rows[start:mid]), budget):
            low = mid
        else:
            high = mid - 1
    return low


def _split_structured(
    block: str,
    structure: _Structure,
    budget: int,
    source_start: int,
) -> list[_RenderedSpan] | None:
    """Split *structure* across cards, reopening its markdown on each one.

    Returns ``None`` when not even one row fits alongside the wrapper, which
    leaves the caller a byte-wise cut as its last resort.
    """
    if not structure.rows:
        return None
    line_starts: list[int] = []
    offset = 0
    for raw_line in block.splitlines(keepends=True):
        line_starts.append(offset)
        offset += len(raw_line)
    row_starts = line_starts[len(structure.head) :][: len(structure.rows)]

    pages: list[_RenderedSpan] = []
    start = 0
    while start < len(structure.rows):
        end = _largest_row_run(structure, start, budget)
        if end == start:
            return None
        owned_start = source_start if start == 0 else source_start + row_starts[start]
        owned_end = (
            source_start + len(block)
            if end == len(structure.rows)
            else source_start + row_starts[end]
        )
        pages.append(
            _RenderedSpan(
                text=structure.card(structure.rows[start:end]),
                source_start=owned_start,
                source_end=owned_end,
            )
        )
        start = end
    return pages


def _hard_split(block: str, budget: int, source_start: int) -> list[_RenderedSpan]:
    """Split a block too large for any single card, preserving every character.

    A fence or table is split at its own row boundaries and rewrapped, so each
    card is well-formed on its own; anything else can only be cut byte-wise.
    """
    structure = _structure(block)
    if structure is not None:
        structured = _split_structured(block, structure, budget, source_start)
        if structured is not None:
            return structured
    pieces: list[_RenderedSpan] = []
    remaining = block
    offset = 0
    while remaining and not _fits(remaining, budget):
        cut = _largest_prefix(remaining, budget)
        if cut == 0:
            raise ValueError(
                f"card budget {budget} cannot hold even the first character of a "
                f"{len(block)}-character block; minimum is "
                f"{spec_bytes(render_card_spec(block[:1], streaming=False))} bytes"
            )
        pieces.append(
            _RenderedSpan(
                text=remaining[:cut],
                source_start=source_start + offset,
                source_end=source_start + offset + cut,
            )
        )
        remaining = remaining[cut:]
        offset += cut
    if remaining:
        pieces.append(
            _RenderedSpan(
                text=remaining,
                source_start=source_start + offset,
                source_end=source_start + len(block),
            )
        )
    return pieces


def safe_prefix(
    text: str,
    *,
    budget: int = FEISHU_CARD_BUDGET_BYTES,
    max_tables: int = FEISHU_CARD_MAX_TABLES,
    suffix: str = "",
    streaming: bool = False,
) -> str:
    """Return the longest block-aligned prefix of *text* whose card fits.

    The result is always a literal prefix, because the caller slices the
    remainder off by its length. A fence or a table that cannot fit on its own
    gets no prefix at all: any literal cut inside it would leave the remainder
    holding an unpaired fence or a headerless table, so the caller is better
    served by the paginator, which rewraps both.
    """
    spans = _block_spans(text)
    best = ""
    tables = 0
    for span in spans:
        next_tables = tables + (1 if _is_table(span.text) else 0)
        if next_tables > max_tables or not _fits(
            text[: span.end] + suffix, budget, streaming=streaming
        ):
            break
        best, tables = text[: span.end], next_tables
    if best:
        return best
    if not spans or _structure(spans[0].text) is not None:
        return ""
    cut = text[: _largest_prefix(text, budget, suffix=suffix, streaming=streaming)]
    if not _fits(cut + suffix, budget, streaming=streaming) or table_count(cut) > max_tables:
        return ""
    return cut


def paginate(
    text: str,
    *,
    budget: int = FEISHU_CARD_BUDGET_BYTES,
    max_tables: int = FEISHU_CARD_MAX_TABLES,
) -> list[CardPage]:
    """Split *text* into cards that each fit the byte budget and the table cap."""
    if not _fits("", budget):
        raise ValueError(
            f"card budget {budget} cannot hold an even empty card; "
            f"minimum is {spec_bytes(render_card_spec('', streaming=False))} bytes"
        )
    if not text.strip():
        return []

    pages: list[_RenderedSpan] = []
    current = ""
    current_start = 0
    current_end = 0
    tables = 0
    cursor = 0

    for span in _block_spans(text):
        unit_start = cursor
        gap = text[cursor : span.start]
        block = span.text
        cursor = span.end
        if not _fits(block, budget):
            split_block = _hard_split(block, budget, span.start)
            if current:
                if gap and _fits(current + gap, budget):
                    current += gap
                    current_end = span.start
                    gap = ""
                pages.append(
                    _RenderedSpan(
                        text=current,
                        source_start=current_start,
                        source_end=current_end,
                    )
                )
                current, tables = "", 0
            if gap:
                first = split_block[0]
                if _fits(gap + first.text, budget):
                    split_block[0] = _RenderedSpan(
                        text=gap + first.text,
                        source_start=unit_start,
                        source_end=first.source_end,
                    )
                else:
                    pages.extend(_hard_split(gap, budget, unit_start))
            pages.extend(split_block)
            continue

        next_tables = tables + (1 if _is_table(block) else 0)
        unit = gap + block
        unit_end = span.end
        candidate = current + unit
        fits = _fits(candidate, budget)
        if not fits or (current and next_tables > max_tables):
            if current:
                pages.append(
                    _RenderedSpan(
                        text=current,
                        source_start=current_start,
                        source_end=current_end,
                    )
                )
            if not _fits(unit, budget):
                if gap:
                    pages.extend(_hard_split(gap, budget, unit_start))
                unit = block
                unit_start = span.start
            current, tables = unit, 1 if _is_table(block) else 0
            current_start, current_end = unit_start, unit_end
            continue
        if not current:
            current_start = unit_start
        current, tables = candidate, next_tables
        current_end = unit_end

    tail = text[cursor:]
    if tail:
        if _fits(current + tail, budget):
            if not current:
                current_start = cursor
            current += tail
            current_end = len(text)
        else:
            if current:
                pages.append(
                    _RenderedSpan(
                        text=current,
                        source_start=current_start,
                        source_end=current_end,
                    )
                )
                current = ""
            pages.extend(_hard_split(tail, budget, cursor))
    if current:
        pages.append(
            _RenderedSpan(
                text=current,
                source_start=current_start,
                source_end=current_end,
            )
        )

    if (
        not pages
        or pages[0].source_start != 0
        or pages[-1].source_end != len(text)
        or any(left.source_end != right.source_start for left, right in zip(pages, pages[1:]))
    ):
        raise RuntimeError("card page source ranges do not partition the input")

    return [
        CardPage(
            text=span.text,
            index=index,
            source_start=span.source_start,
            source_end=span.source_end,
        )
        for index, span in enumerate(pages, 1)
    ]
