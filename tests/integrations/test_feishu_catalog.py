"""Feishu's place in the integration catalog."""

from __future__ import annotations

from integrations import catalog
from integrations._catalog_impl import resolve_effective_integrations
from integrations.registry import INTEGRATION_SPECS, SUPPORTED_VERIFY_SERVICES


def test_feishu_has_a_registry_spec() -> None:
    spec = next(s for s in INTEGRATION_SPECS if s.service == "feishu")

    assert spec.has_verifier is True
    assert spec.direct_effective is True
    assert spec.setup_order == 57
    assert spec.verify_order == 102


def test_feishu_is_a_supported_verify_service() -> None:
    assert "feishu" in SUPPORTED_VERIFY_SERVICES


def test_env_gate_reports_feishu_once_the_app_id_is_set(monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_1")

    assert "feishu" in catalog.load_env_integration_services()


def test_env_gate_stays_silent_without_the_app_id(monkeypatch) -> None:
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)

    assert "feishu" not in catalog.load_env_integration_services()


def test_effective_view_carries_the_feishu_config(monkeypatch) -> None:
    """The gateway resolves Feishu through this view, not the raw env."""
    monkeypatch.setenv("FEISHU_APP_ID", "cli_1")
    monkeypatch.setenv("FEISHU_APP_SECRET", "feishu-secret")

    effective = resolve_effective_integrations(store_integrations=[])

    entry = effective.get("feishu")
    assert entry is not None, "feishu was not published as an effective integration"
    assert entry["config"]["app_id"] == "cli_1"
