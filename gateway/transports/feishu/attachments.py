"""Download and inline resources shared in Feishu messages into the turn prompt.

Mirrors ``gateway.transports.slack.processing.attachments``: text-like files are
decoded and inlined with obvious secrets scrubbed, images are described once by a
vision model and the description inlined, and anything else is named only. This
keeps the turn pipeline text-only while still surfacing attachment content.

What a payload *means* — which ``file_key``/``image_key`` is downloadable, what
the resource URL is, whether a filename is text — belongs to
:mod:`integrations.feishu.inbound_content`. This module owns only the transport
policy: how much gets fetched, and what happens to the bytes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import lark_oapi as lark
from lark_oapi.core.token import TokenManager

from config.constants.feishu import (
    FEISHU_IMAGE_MAX_BYTES,
    FEISHU_RESOURCE_HOST_SUFFIXES,
    FEISHU_RESOURCE_TIMEOUT_SECONDS,
    FEISHU_RESOURCE_TYPE_IMAGE,
    FEISHU_TEXT_FILE_MAX_BYTES,
)
from config.constants.gateway import ATTACHMENT_MAX_TOTAL_CHARS
from core.llm.image_description import describe_image_via_provider, is_supported_image
from gateway.core.attachments.fetch import (
    DownloadedAttachment,
    download_attachment_with_metadata,
)
from gateway.core.attachments.inline import (
    budgeted_section,
    join_attachment_sections,
    truncate_attachment_text,
)
from integrations.feishu import ResourceRef, classify_file, resource_url

logger = logging.getLogger(__name__)

# Fetches a resource by URL, byte cap and whether the head is enough; None on failure.
Downloader = Callable[[str, int, bool], "DownloadedAttachment | None"]
# Describes an image's bytes (mimetype) as text; None when it cannot.
Describer = Callable[[bytes, str], "str | None"]

_LOG_PREFIX = "[feishu-files]"


def _read_as(ref: ResourceRef) -> str:
    """How ``ref`` should be read: ``"image"``, ``"text"`` or ``"binary"``.

    An image reference carries no filename — Feishu only ever hands back an
    ``image_key`` — so its kind is the sole signal. A file reference is
    classified by its name instead.
    """
    if ref.kind == FEISHU_RESOURCE_TYPE_IMAGE:
        return "image"
    return classify_file(ref.name)


def _policy(kind: str) -> tuple[int, bool] | None:
    """The byte cap and head-keeping for ``kind``, or ``None`` when never fetched."""
    if kind == "text":
        return FEISHU_TEXT_FILE_MAX_BYTES, True
    if kind == "image":
        return FEISHU_IMAGE_MAX_BYTES, False
    return None


def _decode(data: bytes) -> str:
    """Decode a text file's bytes, falling back to latin-1 rather than failing."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _render_resource(
    message_id: str,
    ref: ResourceRef,
    remaining: int,
    *,
    downloader: Downloader,
    describer: Describer,
) -> tuple[str, int]:
    """Render one resource as a prompt section plus the characters it consumed.

    Every failure path renders one line that costs no budget, so a resource the
    agent cannot read is still visible to it as something that was sent. The
    caller's turn must never fail because an attachment did.
    """
    label = ref.name or ref.kind
    kind = _read_as(ref)
    policy = _policy(kind)
    if policy is None:
        return f"- {label} — not readable", 0
    if remaining <= 0:
        return f"- {label} — omitted (attachment budget exhausted)", 0
    max_bytes, keep_partial = policy
    downloaded = downloader(resource_url(message_id, ref), max_bytes, keep_partial)
    if downloaded is None:
        return f"- {label} — could not be downloaded", 0
    content_type = downloaded.content_type
    if is_supported_image(content_type):
        description = describer(downloaded.data, content_type)
        if not description:
            return f"- {label} ({content_type}) — image could not be described", 0
        return budgeted_section(
            f"--- image: {label} (vision description) ---", description, remaining
        )
    if kind == "text":
        body = truncate_attachment_text(_decode(downloaded.data))
        return budgeted_section(f"--- attached file: {label} ---", body, remaining)
    return f"- {label} ({content_type or 'binary'}) — not readable", 0


def build_attachments_context(
    message_id: str,
    refs: tuple[ResourceRef, ...],
    *,
    downloader: Downloader,
    describer: Describer = describe_image_via_provider,
) -> str:
    """Render a message's resources as a text block appended to the turn prompt.

    Text-like files are inlined with secrets scrubbed, images become a one-shot
    vision description, and anything else is named only. Per-file and total
    character caps bound what is added. ``downloader`` has no default because the
    real one needs the app's credentials, and a silent no-op default would turn
    every attachment into a "could not be downloaded" line. Returns "" for no
    resources.
    """
    if not refs:
        return ""
    sections: list[str] = []
    remaining = ATTACHMENT_MAX_TOTAL_CHARS
    for ref in refs:
        section, consumed = _render_resource(
            message_id, ref, remaining, downloader=downloader, describer=describer
        )
        remaining -= consumed
        sections.append(section)
    return join_attachment_sections(sections)


def feishu_resource_downloader(app_id: str, app_secret: str) -> Downloader:
    """A downloader that fetches message resources as the app itself.

    One client serves every attachment; the SDK caches the tenant token per app
    id, so the credential is fetched once per process rather than per file. An
    authentication failure degrades to ``None`` — the caller renders it as a
    line in the prompt instead of failing the turn.
    """
    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()

    def _download(url: str, max_bytes: int, keep_partial: bool) -> DownloadedAttachment | None:
        try:
            token = TokenManager.get_self_tenant_token(client.config)
        except Exception as exc:
            logger.warning("%s tenant token unavailable: %s", _LOG_PREFIX, type(exc).__name__)
            return None
        if not token:
            logger.warning("%s tenant token empty; attachment skipped", _LOG_PREFIX)
            return None
        return download_attachment_with_metadata(
            url,
            authorization=f"Bearer {token}",
            host_suffixes=FEISHU_RESOURCE_HOST_SUFFIXES,
            max_bytes=max_bytes,
            timeout=FEISHU_RESOURCE_TIMEOUT_SECONDS,
            log_prefix=_LOG_PREFIX,
            keep_partial=keep_partial,
        )

    return _download


__all__ = [
    "Describer",
    "Downloader",
    "build_attachments_context",
    "feishu_resource_downloader",
]
