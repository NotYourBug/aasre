"""Exact target capabilities for Feishu agent-initiated delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config.constants.feishu import FEISHU_RECEIVE_ID_TYPES

type OutboundTargetPair = tuple[str, str]


@dataclass(frozen=True)
class FeishuOutboundTarget:
    receive_id_type: str
    receive_id: str
    source: str

    @property
    def canonical(self) -> str:
        return f"{self.receive_id_type}:{self.receive_id}"


def _parse_pair(value: str) -> OutboundTargetPair:
    kind, separator, identifier = value.partition(":")
    kind = kind.strip()
    identifier = identifier.strip()
    if not separator or kind not in FEISHU_RECEIVE_ID_TYPES or not identifier:
        raise ValueError("Invalid Feishu outbound target")
    return kind, identifier


def parse_outbound_targets(raw: str) -> frozenset[OutboundTargetPair]:
    """Parse exact configured capabilities, rejecting every malformed nonempty entry."""
    pairs: set[OutboundTargetPair] = set()
    for item in raw.split(","):
        if item.strip():
            pairs.add(_parse_pair(item))
    return frozenset(pairs)


def resolve_outbound_target(
    target: str,
    *,
    default_receive_id_type: str,
    default_receive_id: str,
    allowed_targets: frozenset[OutboundTargetPair],
    gateway_platform: str = "",
    gateway_chat_id: str = "",
    require_chat_id: bool = False,
) -> FeishuOutboundTarget:
    """Resolve one alias or exact capability without widening delivery authority."""
    value = target.strip()
    default_pair: OutboundTargetPair | None = None
    if default_receive_id.strip():
        default_pair = _parse_pair(
            f"{default_receive_id_type.strip()}:{default_receive_id.strip()}"
        )

    if value == "current":
        if gateway_platform != "feishu" or not gateway_chat_id.strip():
            raise PermissionError("Current Feishu chat is unavailable")
        pair = ("chat_id", gateway_chat_id.strip())
        source = "current"
    elif value == "default":
        if default_pair is None:
            raise ValueError("Default Feishu target is not configured")
        pair = default_pair
        source = "default"
    else:
        pair = _parse_pair(value)
        if pair != default_pair and pair not in allowed_targets:
            raise PermissionError("Feishu outbound target is not authorized")
        source = "default" if pair == default_pair else "allowlisted"

    if require_chat_id and pair[0] != "chat_id":
        raise ValueError("Feishu replies require a chat_id target")
    return FeishuOutboundTarget(pair[0], pair[1], source)


def reauthorize_approved_target(
    canonical_target: str,
    *,
    default_receive_id_type: str,
    default_receive_id: str,
    allowed_targets: frozenset[OutboundTargetPair],
    gateway_platform: str = "",
    gateway_chat_id: str = "",
    require_chat_id: bool = False,
) -> FeishuOutboundTarget:
    """Recheck one already approved exact target without resolving aliases again."""
    if gateway_platform and gateway_platform != "feishu":
        raise PermissionError("Feishu tools are unavailable in this gateway")
    pair = _parse_pair(canonical_target)
    if gateway_platform == "feishu" and pair == ("chat_id", gateway_chat_id.strip()):
        if require_chat_id and pair[0] != "chat_id":
            raise ValueError("Feishu replies require a chat_id target")
        return FeishuOutboundTarget(pair[0], pair[1], "current")
    return resolve_outbound_target(
        canonical_target,
        default_receive_id_type=default_receive_id_type,
        default_receive_id=default_receive_id,
        allowed_targets=allowed_targets,
        gateway_platform=gateway_platform,
        gateway_chat_id=gateway_chat_id,
        require_chat_id=require_chat_id,
    )


def resolve_target_from_view(
    target: str, view: dict[str, Any], *, require_chat_id: bool = False
) -> FeishuOutboundTarget:
    """Resolve a public target from the current integration and gateway view."""
    config = view.get("feishu")
    if not isinstance(config, dict):
        raise ValueError("Feishu is not configured")
    platform = str(view.get("_gateway_platform") or "")
    if platform and platform != "feishu":
        raise PermissionError("Feishu tools are unavailable in this gateway")
    return resolve_outbound_target(
        target,
        default_receive_id_type=str(config.get("receive_id_type") or "chat_id"),
        default_receive_id=str(config.get("receive_id") or ""),
        allowed_targets=parse_outbound_targets(str(config.get("allowed_outbound_targets") or "")),
        gateway_platform=platform,
        gateway_chat_id=str(view.get("_gateway_chat_id") or ""),
        require_chat_id=require_chat_id,
    )


def has_outbound_target(view: dict[str, Any], *, require_chat_id: bool = False) -> bool:
    """Return whether this turn has any exact target of the required kind."""
    config = view.get("feishu")
    if not isinstance(config, dict):
        return False
    if (
        not str(config.get("app_id") or "").strip()
        or not str(config.get("app_secret") or "").strip()
    ):
        return False
    platform = str(view.get("_gateway_platform") or "")
    if platform and platform != "feishu":
        return False
    try:
        allowed = parse_outbound_targets(str(config.get("allowed_outbound_targets") or ""))
    except ValueError:
        return False
    if platform == "feishu" and str(view.get("_gateway_chat_id") or "").strip():
        return True
    try:
        resolve_outbound_target(
            "default",
            default_receive_id_type=str(config.get("receive_id_type") or "chat_id"),
            default_receive_id=str(config.get("receive_id") or ""),
            allowed_targets=allowed,
            gateway_platform=platform,
            gateway_chat_id=str(view.get("_gateway_chat_id") or ""),
            require_chat_id=require_chat_id,
        )
        return True
    except (ValueError, PermissionError):
        pass
    return any(kind == "chat_id" for kind, _ in allowed) if require_chat_id else bool(allowed)


__all__ = [
    "FeishuOutboundTarget",
    "OutboundTargetPair",
    "parse_outbound_targets",
    "has_outbound_target",
    "reauthorize_approved_target",
    "resolve_outbound_target",
    "resolve_target_from_view",
]
