"""Boot-time messaging source metadata, independent of vendor prompt factories."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MessagingSource:
    """Classify a messaging tool source and its optional inbound platform."""

    source: str
    inbound_platform: str | None = None


_sources: dict[str, MessagingSource] = {}
_inbound_platforms: set[str] = set()


def register_messaging_source(source: str, *, inbound_platform: str | None = None) -> None:
    """Register exact tool-source metadata at boot."""
    _sources[source] = MessagingSource(source, inbound_platform)
    _inbound_platforms.clear()
    _inbound_platforms.update(
        entry.inbound_platform for entry in _sources.values() if entry.inbound_platform is not None
    )


def registered_messaging_sources() -> tuple[MessagingSource, ...]:
    """Return messaging metadata in registration order."""
    return tuple(_sources.values())


def recognized_messaging_platform(platform: object) -> str | None:
    """Return the normalized inbound platform when registered."""
    if not isinstance(platform, str):
        return None
    normalized = platform.strip().lower()
    return normalized if normalized in _inbound_platforms else None


def messaging_source_allowed(source: str, *, surface: str, active_platform: str | None) -> bool:
    """Preserve shell and non-messaging tools; gate gateway messaging sources."""
    entry = _sources.get(source)
    if surface != "gateway" or entry is None:
        return True
    active = recognized_messaging_platform(active_platform)
    return active is not None and entry.inbound_platform == active


def inactive_messaging_sources(*, surface: str, active_platform: str | None) -> frozenset[str]:
    """Return registered sources withheld on this surface and platform."""
    return frozenset(
        source
        for source in _sources
        if not messaging_source_allowed(source, surface=surface, active_platform=active_platform)
    )


def reset() -> None:
    """Clear messaging-source metadata for isolated boot tests."""
    _sources.clear()
    _inbound_platforms.clear()
