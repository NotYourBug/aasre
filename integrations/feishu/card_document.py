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


def _is_table(block: str) -> bool:
    """Return whether *block* is a GFM table."""
    lines = block.splitlines()
    return len(lines) >= 2 and _DELIMITER.match(lines[1]) is not None


def _blocks(text: str) -> list[str]:
    """Split markdown into units that must not break across cards.

    A fenced code block is one unit even when it contains blank lines; outside a
    fence, a blank line ends the unit.
    """
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(_FENCE):
            in_fence = not in_fence
            current.append(line)
            continue
        if not in_fence and not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _fits(text: str, budget: int) -> bool:
    return spec_bytes(render_card_spec(text, streaming=False)) <= budget


def _largest_prefix(text: str, budget: int) -> int:
    """Return the longest character count whose card fits, never below one."""
    low, high = 1, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _fits(text[:mid], budget):
            low = mid
        else:
            high = mid - 1
    return low


def _hard_split(block: str, budget: int) -> list[str]:
    """Split a block too large to fit any single card, preserving every character."""
    pieces: list[str] = []
    remaining = block
    while remaining and not _fits(remaining, budget):
        cut = _largest_prefix(remaining, budget)
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        pieces.append(remaining)
    return pieces


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
