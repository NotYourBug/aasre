"""Bounded RCA summary sections for chat-notification channels.

Chat platforms cap a message at 4096 characters and the transports
tail-truncate to fit. The RCA body ends with "What to do next" and the stats
block, so an unbounded root cause would push exactly the actionable sections
off the end. Budget each section instead: the worst case below stays under
the cap, so the tail always survives.

Email keeps the full report; chat channels carry this bounded summary and
point the reader at ``/background show`` for the rest.
"""

from __future__ import annotations

from core.domain.background_investigations import BackgroundInvestigationRecord

_COMMAND_CHARS = 200
_ROOT_CAUSE_CHARS = 1000
_ITEM_CHARS = 240
_MAX_ITEMS = 5
_MARKDOWN_LITERAL_TRANSLATION = str.maketrans(
    {char: chr(ord(char) + 0xFEE0) for char in "\\`*_{}[]()#!|<>~&+-="}
)


def _markdown_code(value: str) -> str:
    """Keep generated inline-code spans closed when input contains backticks."""
    return " ".join(value.split()).replace("`", "'")


def _markdown_literal(value: str) -> str:
    """Neutralize markup without expanding the bounded summary fields."""
    return " ".join(value.split()).translate(_MARKDOWN_LITERAL_TRANSLATION)


def _markdown_items(values: tuple[str, ...]) -> list[str]:
    """Render bounded item groups with a stable empty fallback."""
    return [f"- {_markdown_literal(value)}" for value in values] or ["- Unavailable"]


def summary_sections(
    record: BackgroundInvestigationRecord,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    """Return the RCA sections trimmed to fit one chat message."""
    from infrastructure.text.truncation import truncate

    def _items(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(truncate(value, _ITEM_CHARS, suffix="…") for value in values[:_MAX_ITEMS])

    return (
        truncate(record.command, _COMMAND_CHARS, suffix="…"),
        truncate(record.root_cause, _ROOT_CAUSE_CHARS, suffix="…"),
        _items(record.top_analysis),
        _items(record.next_steps),
    )


def format_background_rca_markdown(record: BackgroundInvestigationRecord) -> str:
    """Render the bounded background-RCA summary as canonical Markdown."""
    command, root_cause, top_analysis, next_steps = summary_sections(record)
    lines = [
        "# OpenSRE background investigation completed",
        "",
        f"**Task ID:** `{_markdown_code(record.task_id)}`",
        f"**Command:** `{_markdown_code(command)}`",
        "",
        "## Root cause",
        _markdown_literal(root_cause) or "Unavailable",
        "",
        "## Top analysis",
        *_markdown_items(top_analysis),
        "",
        "## What to do next",
        *_markdown_items(next_steps),
        "",
        "## Internal stats",
        f"- tool calls: {int(record.stats.get('tool_call_count', 0) or 0)}",
        f"- investigation loops: {int(record.stats.get('investigation_loop_count', 0) or 0)}",
        f"- validity score: {float(record.stats.get('validity_score', 0.0) or 0.0):.2f}",
    ]
    return "\n".join(lines)


__all__ = ["format_background_rca_markdown", "summary_sections"]
