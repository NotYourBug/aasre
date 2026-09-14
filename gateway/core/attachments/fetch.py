"""Credential-safe, size-capped attachment downloads for gateway transports.

Attachment URLs arrive inside inbound events, so a fetch that carries a bot
token must never send it to a host the vendor does not own — an
attacker-influenced URL would otherwise be handed the credential on the very
first request. The host is therefore allowlisted before anything is opened.

Redirects are then resolved here rather than by ``follow_redirects=True`` so the
allowlist is re-checked on every hop and the download stops before opening a
connection outside it. httpx does strip ``Authorization`` across origins on its
own, but that is a library detail to depend on rather than enforce, and it
carves out http-to-https redirects, which keep the header.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

# Hops we chase before giving up — enough for a vendor CDN handoff, not a loop.
_MAX_REDIRECTS = 5

_REDIRECT_STATUSES = frozenset(
    {
        HTTPStatus.MOVED_PERMANENTLY,
        HTTPStatus.FOUND,
        HTTPStatus.SEE_OTHER,
        HTTPStatus.TEMPORARY_REDIRECT,
        HTTPStatus.PERMANENT_REDIRECT,
    }
)


@dataclass(frozen=True)
class DownloadedAttachment:
    """A fetched attachment's bytes, its declared type, and whether the cap cut it."""

    data: bytes
    content_type: str
    truncated: bool


def is_allowed_host(url: str, host_suffixes: tuple[str, ...]) -> bool:
    """Whether ``url`` is an ``https`` URL on one of ``host_suffixes``.

    Matching is on the parsed hostname, so ``vendor.com.evil.example`` never
    passes as ``vendor.com``, and plain ``http`` is rejected outright.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in host_suffixes)


def _next_url(current_url: str, location: str, log_prefix: str) -> str | None:
    """Resolve a redirect ``location`` against ``current_url``; None if unusable."""
    if not location:
        logger.warning("%s redirect without a location header", log_prefix)
        return None
    try:
        target = urljoin(current_url, location)
        scheme = urlparse(target).scheme
    except ValueError:
        logger.warning("%s redirect target is invalid", log_prefix)
        return None
    if scheme != "https":
        logger.warning("%s redirect to a non-https target rejected", log_prefix)
        return None
    return target


def _read_capped(
    response: httpx.Response,
    max_bytes: int,
    log_prefix: str,
    *,
    keep_partial: bool = False,
) -> tuple[bytes, bool] | None:
    """Drain a streamed response up to ``max_bytes``; None if the body is dropped.

    Returns ``(data, truncated)``. ``truncated`` is True only when the cap cut the
    body, never when it happened to end exactly at the cap. With ``keep_partial``
    the read stops at the cap and keeps the head; otherwise an oversized body is
    dropped whole.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            if not keep_partial:
                logger.info("%s payload exceeds %d bytes; skipped", log_prefix, max_bytes)
                return None
            remaining = max_bytes - (total - len(chunk))
            if remaining > 0:
                chunks.append(chunk[:remaining])
            logger.info("%s payload exceeds %d bytes; truncated", log_prefix, max_bytes)
            return b"".join(chunks), True
        chunks.append(chunk)
    return b"".join(chunks), False


def download_attachment_with_metadata(
    url: str,
    *,
    authorization: str,
    host_suffixes: tuple[str, ...],
    max_bytes: int,
    timeout: float,
    log_prefix: str,
    keep_partial: bool = False,
    max_redirects: int = _MAX_REDIRECTS,
) -> DownloadedAttachment | None:
    """GET ``url`` with the credential pinned to ``host_suffixes``; None on failure.

    The first request is refused unless ``url`` is an https URL on an allowlisted
    host, and each redirect is followed manually only while its target remains on
    an allowlisted host. Returns the final hop's body together with its declared
    ``Content-Type`` — the only mimetype signal for a vendor payload that carries
    no filename. Set ``keep_partial`` to keep the head of a body that exceeds
    ``max_bytes`` (useful when the leading bytes are the informative part); the
    default drops such a body entirely.
    """
    if not (url and authorization):
        return None
    if not is_allowed_host(url, host_suffixes):
        logger.warning("%s attachment URL host rejected", log_prefix)
        return None
    current_url = url
    try:
        for _hop in range(max_redirects + 1):
            with httpx.stream(
                "GET",
                current_url,
                headers={"Authorization": authorization},
                follow_redirects=False,
                timeout=timeout,
            ) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    target = _next_url(
                        current_url, response.headers.get("location", ""), log_prefix
                    )
                    if target is None:
                        return None
                    if not is_allowed_host(target, host_suffixes):
                        logger.warning("%s redirect target host rejected", log_prefix)
                        return None
                    current_url = target
                    continue
                if response.status_code != HTTPStatus.OK:
                    logger.warning("%s download HTTP %s", log_prefix, response.status_code)
                    return None
                read = _read_capped(response, max_bytes, log_prefix, keep_partial=keep_partial)
                if read is None:
                    return None
                data, truncated = read
                return DownloadedAttachment(
                    data=data,
                    content_type=response.headers.get("content-type", ""),
                    truncated=truncated,
                )
    except httpx.HTTPError as exc:
        logger.warning("%s download failed: %s", log_prefix, type(exc).__name__)
        return None
    logger.warning("%s download exceeded %d redirects", log_prefix, max_redirects)
    return None


def download_attachment(
    url: str,
    *,
    authorization: str,
    host_suffixes: tuple[str, ...],
    max_bytes: int,
    timeout: float,
    log_prefix: str,
    max_redirects: int = _MAX_REDIRECTS,
) -> bytes | None:
    """GET ``url`` with the credential pinned to ``host_suffixes``; None on failure.

    The first request is refused unless ``url`` is an https URL on an allowlisted
    host. Each redirect is followed manually only while its target remains on an
    allowlisted host. Oversized bodies are dropped rather than truncated.
    """
    downloaded = download_attachment_with_metadata(
        url,
        authorization=authorization,
        host_suffixes=host_suffixes,
        max_bytes=max_bytes,
        timeout=timeout,
        log_prefix=log_prefix,
        max_redirects=max_redirects,
    )
    return None if downloaded is None else downloaded.data


__all__ = [
    "DownloadedAttachment",
    "download_attachment",
    "download_attachment_with_metadata",
    "is_allowed_host",
]
