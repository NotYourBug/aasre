"""Resolve the owning principal for a Feishu gateway turn.

Feishu turns attribute data to the silo organization (``ORGANIZATION_ID``) and
key per-actor sessions by the sender's open_id — the same border as
Slack/Discord/Telegram/Buzz (org + actor). Must not import peer transports'
principal modules (peer isolation).
"""

from __future__ import annotations

from config.principal import Actor, Principal, StorageScope
from gateway.core.billing.credits_client import organization_id_for_silo


class PrincipalResolutionError(RuntimeError):
    """Raised when the owner of a Feishu turn's data cannot be established."""


def resolve_feishu_principal() -> Principal:
    """Principal for a Feishu turn: the organization this deployment serves."""
    silo_org = organization_id_for_silo()
    if not silo_org:
        raise PrincipalResolutionError(
            "Feishu turn cannot be resolved: no organization is configured for this deployment"
        )
    return Principal.org(silo_org)


def feishu_scope(principal: Principal, open_id: str) -> StorageScope:
    """Pair an organization with the Feishu sender acting in it."""
    return StorageScope(principal=principal, actor=Actor(id=open_id))


def resolve_feishu_scope(*, open_id: str) -> StorageScope:
    """Owning principal and acting member for one Feishu turn."""
    sender = (open_id or "").strip()
    if not sender:
        raise PrincipalResolutionError("Feishu turn carried no sender open_id")
    return feishu_scope(resolve_feishu_principal(), sender)


__all__ = [
    "PrincipalResolutionError",
    "feishu_scope",
    "resolve_feishu_principal",
    "resolve_feishu_scope",
]
