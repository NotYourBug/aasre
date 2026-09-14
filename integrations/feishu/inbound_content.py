"""Parse an inbound Feishu message's ``content`` into text and resource references.

Feishu delivers a screenshot as a rich-text (``post``) message, not an image
message, because the client composes the required bot @-mention and the image
into one payload — so the image's ``image_key`` exists only inside the node tree
this module walks. A ``file`` message is a superset that covers text, PDF, audio
and video alike, discriminated by filename. Both facts are captured from the
live app, not inferred from the API reference.

Pure by construction: no client, no network, no ``gateway`` import (Tier 3 may
not import Tier 1). ``gateway.transports.feishu.attachments`` does the I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from config.constants.feishu import (
    FEISHU_BINARY_FILE_SUFFIXES,
    FEISHU_RESOURCE_TYPE_FILE,
    FEISHU_RESOURCE_TYPE_IMAGE,
    FEISHU_TEXT_FILE_SUFFIXES,
)

_RESOURCE_HOST = "https://open.feishu.cn"
_RESOURCE_PATH = "/open-apis/im/v1/messages/{message_id}/resources/{file_key}"

#: Suffixes among the binary set that vision can read once downloaded.
_IMAGE_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})

#: Message types that earn a turn. Everything else — stickers, system notices,
#: shared contacts, forwarded bundles — stays silent rather than answering a
#: user who did not address the bot.
TRACKED_MESSAGE_TYPES = frozenset({"text", "post", "image", "file", "media"})


@dataclass(frozen=True)
class ResourceRef:
    """One downloadable resource in a message: which endpoint, which key, its name."""

    kind: str
    key: str
    name: str = ""


def resource_refs(message_type: str, content: dict[str, Any]) -> tuple[ResourceRef, ...]:
    """Every resource attached to ``message_type``, in the order it was sent."""
    if message_type == "post":
        return _post_refs(content)
    if message_type in {"image", "media"}:
        # A video is useless to a text model; its cover frame is not, and Feishu
        # serves that frame from the image endpoint.
        return _ref_if(FEISHU_RESOURCE_TYPE_IMAGE, _text(content.get("image_key")))
    if message_type == "file":
        name = _text(content.get("file_name"))
        return _ref_if(FEISHU_RESOURCE_TYPE_FILE, _text(content.get("file_key")), name=name)
    return ()


def flatten_post(content: dict[str, Any], *, bot_keys: frozenset[str]) -> str:
    """Flatten a ``post`` node tree into plain text.

    Paragraphs become lines; ``text``, link and ``code_block`` runs contribute
    their content. An ``at`` node naming the bot is dropped so the prompt does
    not open with a placeholder mention, while another member's mention is kept
    as ``@name`` — the same rule ``strip_leading_feishu_mentions`` applies to
    text messages. An unrecognised tag is skipped without taking its siblings
    with it; losing a whole message to one new node type would be the exact
    failure this slice exists to remove.
    """
    lines: list[str] = []
    title = _text(content.get("title"))
    if title:
        lines.append(title)
    for paragraph in _paragraphs(content):
        runs: list[str] = []
        for node in paragraph:
            if not isinstance(node, dict):
                continue
            tag = node.get("tag")
            if tag == "at":
                user_id = _text(node.get("user_id"))
                if user_id in bot_keys:
                    continue
                name = _text(node.get("user_name")) or user_id
                if name:
                    runs.append(f"@{name}")
                continue
            if tag in {"a", "text"} or tag == "code_block":
                runs.append(_text(node.get("text")))
            elif tag == "emotion":
                runs.append(_text(node.get("emoji")))
        line = "".join(runs).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def resource_url(message_id: str, ref: ResourceRef) -> str:
    """The absolute download URL for ``ref`` inside ``message_id``.

    Both identifiers come from the event, so each is percent-encoded: a key
    carrying ``?`` or ``#`` would otherwise reshape the query string and lose the
    ``type`` parameter the endpoint requires.
    """
    path = _RESOURCE_PATH.format(
        message_id=quote(message_id, safe=""), file_key=quote(ref.key, safe="")
    )
    return f"{_RESOURCE_HOST}{path}?type={ref.kind}"


def classify_file(name: str) -> str:
    """Whether a filename should be read as text, as an image, or not read at all."""
    suffix = _suffix(name)
    if suffix in FEISHU_TEXT_FILE_SUFFIXES:
        return "text"
    if suffix in FEISHU_BINARY_FILE_SUFFIXES:
        return "image" if suffix in _IMAGE_SUFFIXES else "binary"
    return "text"


def is_known_text_file(name: str) -> bool:
    """Whether ``name``'s suffix is one we read as text without corroboration.

    An unrecognised suffix also classifies as text — one bounded read beats
    guessing wrong on ``app.log.1`` — but the caller has to corroborate those
    bytes against the response's Content-Type before inlining them.
    """
    return _suffix(name) in FEISHU_TEXT_FILE_SUFFIXES


def _post_refs(content: dict[str, Any]) -> tuple[ResourceRef, ...]:
    found: list[ResourceRef] = []
    for paragraph in _paragraphs(content):
        for node in paragraph:
            if isinstance(node, dict) and node.get("tag") == "img":
                found.append(
                    ResourceRef(kind=FEISHU_RESOURCE_TYPE_IMAGE, key=_text(node.get("image_key")))
                )
    return tuple(ref for ref in found if ref.key)


def _paragraphs(content: dict[str, Any]) -> list[list[Any]]:
    raw = content.get("content")
    if not isinstance(raw, list):
        return []
    return [paragraph for paragraph in raw if isinstance(paragraph, list)]


def _ref_if(kind: str, key: str, *, name: str = "") -> tuple[ResourceRef, ...]:
    return (ResourceRef(kind=kind, key=key, name=name),) if key else ()


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _suffix(name: str) -> str:
    _, dot, tail = name.rpartition(".")
    return f".{tail.lower()}" if dot else ""


__all__ = [
    "ResourceRef",
    "TRACKED_MESSAGE_TYPES",
    "classify_file",
    "flatten_post",
    "is_known_text_file",
    "resource_refs",
    "resource_url",
]
