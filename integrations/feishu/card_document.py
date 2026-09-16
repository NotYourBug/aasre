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

_FENCE = "```"

#: A GFM delimiter row — pipes, dashes, optional alignment colons.
_DELIMITER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")


@dataclass(frozen=True)
class CardPage:
    """One card's worth of markdown. ``index`` is 1-based."""

    text: str
    index: int


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


def _fence_structure(lines: list[str]) -> _Structure | None:
    """Return the fenced-code view of *lines*, or ``None`` when it is no fence."""
    if not lines or not lines[0].lstrip().startswith(_FENCE):
        return None
    rows = lines[1:]
    if rows and rows[-1].strip() == _FENCE:
        rows = rows[:-1]
    return _Structure(head=(lines[0],), rows=tuple(rows), tail=(_FENCE,))


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


def _block_spans(text: str) -> list[tuple[int, str]]:
    """Return ``(end, block)`` for each block of *text*, in order.

    ``end`` is the offset just past the block's last line, so ``text[:end]`` is
    a literal prefix holding that block whole and nothing after it.
    """
    spans: list[tuple[int, str]] = []
    current: list[str] = []
    in_fence = False
    offset = 0
    end = 0
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip(_LINE_TERMINATORS)
        start, offset = offset, offset + len(raw)
        if line.lstrip().startswith(_FENCE):
            in_fence = not in_fence
        elif not in_fence and not line.strip():
            if current:
                spans.append((end, "\n".join(current)))
                current = []
            continue
        current.append(line)
        end = start + len(line)
    if current:
        spans.append((end, "\n".join(current)))
    return spans


def _blocks(text: str) -> list[str]:
    """Split markdown into units that must not break across cards.

    A fenced code block is one unit even when it contains blank lines; outside a
    fence, a blank line ends the unit.
    """
    return [block for _end, block in _block_spans(text)]


def table_count(text: str) -> int:
    """Return the number of GFM tables in *text*."""
    return sum(1 for block in _blocks(text) if _is_table(block))


def _fits(text: str, budget: int) -> bool:
    return spec_bytes(render_card_spec(text, streaming=False)) <= budget


def _largest_prefix(text: str, budget: int) -> int:
    """Return the longest character count whose card fits, or 0 when none does."""
    low, high = 1, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _fits(text[:mid], budget):
            low = mid
        else:
            high = mid - 1
    return low if _fits(text[:low], budget) else 0


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


def _split_structured(structure: _Structure, budget: int) -> list[str] | None:
    """Split *structure* across cards, reopening its markdown on each one.

    Returns ``None`` when not even one row fits alongside the wrapper, which
    leaves the caller a byte-wise cut as its last resort.
    """
    if not structure.rows:
        return None
    pages: list[str] = []
    start = 0
    while start < len(structure.rows):
        end = _largest_row_run(structure, start, budget)
        if end == start:
            return None
        pages.append(structure.card(structure.rows[start:end]))
        start = end
    return pages


def _hard_split(block: str, budget: int) -> list[str]:
    """Split a block too large for any single card, preserving every character.

    A fence or table is split at its own row boundaries and rewrapped, so each
    card is well-formed on its own; anything else can only be cut byte-wise.
    """
    structure = _structure(block)
    if structure is not None:
        structured = _split_structured(structure, budget)
        if structured is not None:
            return structured
    pieces: list[str] = []
    remaining = block
    while remaining and not _fits(remaining, budget):
        cut = _largest_prefix(remaining, budget)
        if cut == 0:
            raise ValueError(
                f"card budget {budget} cannot hold even the first character of a "
                f"{len(block)}-character block; minimum is "
                f"{spec_bytes(render_card_spec(block[:1], streaming=False))} bytes"
            )
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        pieces.append(remaining)
    return pieces


def safe_prefix(
    text: str, *, budget: int = FEISHU_CARD_BUDGET_BYTES, max_tables: int = FEISHU_CARD_MAX_TABLES
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
    for end, block in spans:
        next_tables = tables + (1 if _is_table(block) else 0)
        if next_tables > max_tables or not _fits(text[:end], budget):
            break
        best, tables = text[:end], next_tables
    if best:
        return best
    if not spans or _structure(spans[0][1]) is not None:
        return ""
    cut = text[: _largest_prefix(text, budget)]
    if not _fits(cut, budget) or table_count(cut) > max_tables:
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

    pages: list[list[str]] = []
    current: list[str] = []
    tables = 0

    for block in _blocks(text):
        if not _fits(block, budget):
            if current:
                pages.append(current)
                current, tables = [], 0
            for piece in _hard_split(block, budget):
                pages.append([piece])
            continue

        next_tables = tables + (1 if _is_table(block) else 0)
        candidate = current + [block]
        fits = _fits("\n\n".join(candidate), budget)
        if current and (not fits or next_tables > max_tables):
            pages.append(current)
            current, tables = [block], 1 if _is_table(block) else 0
            continue
        current, tables = candidate, next_tables

    if current:
        pages.append(current)
    return [CardPage(text="\n\n".join(parts), index=i) for i, parts in enumerate(pages, 1)]
