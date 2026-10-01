"""Transport-neutral attachment text rendering for gateway turns."""

from __future__ import annotations

from config.constants.gateway import ATTACHMENT_MAX_FILE_CHARS
from infrastructure.safety.masking.secrets import scrub_secrets as _scrub_secrets

_HEAD_CHARS = 26_000
_TAIL_CHARS = 12_000

_TEXT_MIME_EXACT = frozenset(
    {
        "application/json",
        "application/x-ndjson",
        "application/csv",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/x-sh",
    }
)


def is_text_mimetype(mimetype: str) -> bool:
    mime = mimetype.split(";", 1)[0].strip().lower()
    return mime.startswith("text/") or mime in _TEXT_MIME_EXACT


def truncate_attachment_text(text: str, *, max_chars: int = ATTACHMENT_MAX_FILE_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - _HEAD_CHARS - _TAIL_CHARS
    return f"{text[:_HEAD_CHARS]}\n… [{omitted} characters omitted] …\n{text[-_TAIL_CHARS:]}"


def budgeted_section(header: str, raw_text: str, remaining: int) -> tuple[str, int]:
    """Scrub and truncate ``raw_text`` to the remaining budget under ``header``."""
    body = _scrub_secrets(raw_text)[:remaining]
    return f"{header}\n{body}", len(body)


def join_attachment_sections(sections: list[str]) -> str:
    if not sections:
        return ""
    return "Attached files:\n" + "\n".join(sections)


__all__ = [
    "budgeted_section",
    "is_text_mimetype",
    "join_attachment_sections",
    "truncate_attachment_text",
]
