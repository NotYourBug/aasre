"""Feishu principal / scope resolution."""

from __future__ import annotations

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from gateway.transports.feishu.principal import (
    PrincipalResolutionError,
    resolve_feishu_scope,
)


def test_resolve_scope_refuses_empty_open_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_feishu")
    with pytest.raises(PrincipalResolutionError, match="no sender open_id"):
        resolve_feishu_scope(open_id="")


def test_resolve_scope_refuses_unset_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    with pytest.raises(PrincipalResolutionError, match="no organization"):
        resolve_feishu_scope(open_id="ou_alice")


def test_scope_pairs_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_scope")
    scope = resolve_feishu_scope(open_id="ou_alice")
    assert scope.principal.id == "org_scope"
    assert scope.actor.id == "ou_alice"
