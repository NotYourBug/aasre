"""Feishu's place in the integration catalog."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from integrations import catalog
from integrations._catalog_impl import resolve_effective_integrations
from integrations.feishu import load_chat_credentials_from_env
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


def test_env_view_carries_outbound_targets(monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_1")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
    monkeypatch.setenv("FEISHU_ALLOWED_OUTBOUND_TARGETS", "chat_id:oc_ops")

    effective = resolve_effective_integrations(store_integrations=[])

    assert effective["feishu"]["config"]["allowed_outbound_targets"] == "chat_id:oc_ops"


def _policy_record() -> dict[str, Any]:
    return {
        "id": "feishu-policy",
        "service": "feishu",
        "status": "active",
        "instances": [
            {
                "name": "default",
                "tags": {},
                "credentials": {
                    "identity_policy": {
                        "allowed_user_ids": ["ou_policy"],
                        "inbound_enabled": True,
                    }
                },
            }
        ],
    }


def _env_record() -> dict[str, Any]:
    return {
        "id": "env-feishu",
        "service": "feishu",
        "status": "active",
        "credentials": {
            "app_id": "cli_env",
            "app_secret": "s_env",
            "receive_id": "oc_env",
            "receive_id_type": "chat_id",
            "allowed_open_ids": "ou_env",
            "allowed_outbound_targets": "chat_id:oc_stale",
        },
    }


def test_policy_only_store_preserves_env_config_without_mutation() -> None:
    store_records = [_policy_record()]
    env_records = [_env_record()]
    before = copy.deepcopy((store_records, env_records))

    assert catalog.merge_local_integrations(store_records, env_records) == env_records
    effective = catalog.resolve_effective_integrations(store_records, env_records)

    assert effective["feishu"] == {
        "source": "local env",
        "config": env_records[0]["credentials"],
    }
    assert (store_records, env_records) == before


def test_explicit_store_config_and_empty_targets_still_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _policy_record()
    record["instances"][0]["credentials"].update(
        {
            "app_id": "cli_store",
            "app_secret": "s_store",
            "receive_id": "",
            "allowed_outbound_targets": "",
        }
    )
    store_records, env_records = [record], [_env_record()]
    before = copy.deepcopy((store_records, env_records))
    store_path = tmp_path / "integrations.json"
    store_path.write_text(json.dumps({"version": 2, "integrations": store_records}))
    monkeypatch.setattr("integrations.store.STORE_PATH", store_path)
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "s_env")
    monkeypatch.setenv("FEISHU_CHAT_RECEIVE_ID", "oc_stale")
    monkeypatch.setenv("FEISHU_ALLOWED_OUTBOUND_TARGETS", "chat_id:oc_stale")
    store_bytes = store_path.read_bytes()

    assert catalog.merge_local_integrations(store_records, env_records) == store_records
    effective = catalog.resolve_effective_integrations()
    assert effective["feishu"]["source"] == "local store"
    assert effective["feishu"]["config"]["app_id"] == "cli_store"
    assert effective["feishu"]["config"]["app_secret"] == "s_store"
    assert effective["feishu"]["config"]["receive_id"] == ""
    assert effective["feishu"]["config"]["allowed_outbound_targets"] == ""
    creds = load_chat_credentials_from_env()
    assert creds.app_id == "cli_store"
    assert creds.app_secret == "s_store"
    assert creds.receive_id == ""
    assert creds.allowed_outbound_targets == ""
    assert store_path.read_bytes() == store_bytes
    assert (store_records, env_records) == before


@pytest.mark.parametrize(
    "field", ["app_id", "app_secret", "receive_id", "allowed_outbound_targets"]
)
def test_explicit_blank_app_field_is_not_policy_only(field: str) -> None:
    record = _policy_record()
    record["instances"][0]["credentials"][field] = ""
    store_records, env_records = [record], [_env_record()]
    before = copy.deepcopy((store_records, env_records))

    assert catalog.merge_local_integrations(store_records, env_records) == store_records
    assert "feishu" not in catalog.resolve_effective_integrations(store_records, env_records)
    assert (store_records, env_records) == before


@pytest.mark.parametrize(
    "case",
    ["inactive", "bad-policy", "non-default", "multi-instance", "duplicate", "top-level"],
)
def test_ambiguous_or_invalid_policy_record_keeps_store_precedence(case: str) -> None:
    record = _policy_record()
    store_records = [record]
    if case == "inactive":
        record["status"] = "inactive"
    elif case == "bad-policy":
        record["instances"][0]["credentials"]["identity_policy"]["allowed_user_ids"] = "ou_policy"
    elif case == "non-default":
        record["instances"][0]["name"] = "prod"
    elif case == "multi-instance":
        record["instances"].append({"name": "prod", "tags": {}, "credentials": {}})
    elif case == "duplicate":
        store_records.append(copy.deepcopy(record))
    elif case == "top-level":
        record["receive_id"] = ""
    env_records = [_env_record()]
    before = copy.deepcopy((store_records, env_records))

    assert catalog.merge_local_integrations(store_records, env_records) == [store_records[-1]]
    assert "feishu" not in catalog.resolve_effective_integrations(store_records, env_records)
    assert (store_records, env_records) == before


@pytest.mark.parametrize("env_case", ["absent", "incomplete", "inactive"])
def test_policy_record_without_configured_env_is_preserved(env_case: str) -> None:
    store_records = [_policy_record()]
    env_records = [_env_record()]
    if env_case == "absent":
        env_records = []
    elif env_case == "incomplete":
        env_records[0]["credentials"]["app_secret"] = ""
    else:
        env_records[0]["status"] = "inactive"
    before = copy.deepcopy((store_records, env_records))

    assert catalog.merge_local_integrations(store_records, env_records) == store_records
    assert "feishu" not in catalog.resolve_effective_integrations(store_records, env_records)
    assert (store_records, env_records) == before


def test_other_platform_policy_records_keep_existing_merge_semantics() -> None:
    record = _policy_record()
    record["service"] = "telegram"
    env_record = {"service": "telegram", "status": "active", "credentials": {"bot_token": "stale"}}
    before = copy.deepcopy((record, env_record))

    assert catalog.merge_local_integrations([record], [env_record]) == [record]
    assert (record, env_record) == before


def test_legacy_record_does_not_gain_new_fallback_semantics() -> None:
    record = _policy_record()
    record["credentials"] = record.pop("instances")[0]["credentials"]
    env_records = [_env_record()]
    before = copy.deepcopy((record, env_records))

    assert catalog.merge_local_integrations([record], env_records) == [record]
    assert "feishu" not in catalog.resolve_effective_integrations([record], env_records)
    assert (record, env_records) == before
