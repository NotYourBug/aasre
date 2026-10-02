import json
from pathlib import Path

import pytest

import config.constants.paths as paths
from config.constants import INTEGRATIONS_STORE_PATH_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope, current_scope
from gateway.core.lifecycle.errors import GatewayConfigurationError
from gateway.core.middleware.identity_policy import load_identity_policy, save_identity_policy
from gateway.transports.feishu import settings
from gateway.transports.feishu.inbound_security import is_feedback_actor_authorized
from gateway.transports.feishu.settings import load_feishu_gateway_settings
from integrations import catalog
from integrations.feishu.credentials import FeishuChatCredentials, load_chat_credentials_from_env
from integrations.messaging_security import MessagingIdentityPolicy, authorize_inbound_message
from integrations.store import resolve_store_path


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    with pytest.raises(GatewayConfigurationError):
        load_feishu_gateway_settings()


def test_loads_credentials(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "s_test")
    settings = load_feishu_gateway_settings()
    assert settings.app_id == "cli_test"
    assert settings.app_secret == "s_test"


def test_loads_allowed_open_ids_from_env(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "s_test")
    monkeypatch.setenv("FEISHU_ALLOWED_OPEN_IDS", "ou_alice, ou_bob ,ou_carol")
    settings = load_feishu_gateway_settings()
    assert settings.allowed_open_ids == ["ou_alice", "ou_bob", "ou_carol"]


def test_store_credentials_start_the_worker_without_env(monkeypatch):
    """Guided setup writes the store; the gateway must start from it alone."""
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_ALLOWED_OPEN_IDS", raising=False)
    monkeypatch.setattr(
        settings,
        "load_chat_credentials_from_env",
        lambda: FeishuChatCredentials(
            app_id="cli_store",
            app_secret="s_store",
            receive_id="oc_store",
            receive_id_type="chat_id",
            allowed_open_ids="ou_store",
        ),
    )

    loaded = settings.load_feishu_gateway_settings()

    assert loaded.app_id == "cli_store"
    assert loaded.app_secret == "s_store"
    assert loaded.allowed_open_ids == ["ou_store"]


def test_env_still_configures_the_worker_when_the_store_is_empty(monkeypatch):
    """A .env-only deployment must keep working untouched."""
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "s_env")
    monkeypatch.setenv("FEISHU_ALLOWED_OPEN_IDS", "ou_env")

    loaded = settings.load_feishu_gateway_settings()

    assert loaded.app_id == "cli_env"
    assert loaded.app_secret == "s_env"
    assert loaded.allowed_open_ids == ["ou_env"]


def test_empty_credential_values_raise_instead_of_starting_a_broken_worker(monkeypatch):
    """A half-configured store yields present-but-empty values; that must not start the worker."""
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.setattr(
        settings,
        "load_chat_credentials_from_env",
        lambda: FeishuChatCredentials(
            app_id="cli_partial",
            app_secret="",
            receive_id="",
            receive_id_type="chat_id",
            allowed_open_ids="",
        ),
    )

    with pytest.raises(GatewayConfigurationError):
        settings.load_feishu_gateway_settings()


def test_a_store_record_alone_starts_the_worker_end_to_end(monkeypatch, tmp_path):
    """The spec's chain on the real store file: record → effective view → leaf → settings.

    Every other test here stubs one hop, so a key-name drift between the
    classifier, the effective view, and the leaf would pass all of them.
    """
    store = tmp_path / "integrations.json"
    store.write_text(
        json.dumps(
            {
                "version": 2,
                "integrations": [
                    {
                        "id": "feishu-e2e",
                        "service": "feishu",
                        "status": "active",
                        "instances": [
                            {
                                "name": "default",
                                "tags": {},
                                "credentials": {
                                    "app_id": "cli_from_store",
                                    "app_secret": "s_from_store",
                                    "receive_id": "oc_from_store",
                                    "receive_id_type": "chat_id",
                                    "allowed_open_ids": "ou_from_store",
                                },
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(INTEGRATIONS_STORE_PATH_ENV, str(store))
    for name in (
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_ALLOWED_OPEN_IDS",
        "FEISHU_CHAT_RECEIVE_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    # The gateway settings surface only three of the five keys, so assert the
    # leaf directly for the other two — a drift in ``receive_id`` or
    # ``receive_id_type`` between the classifier and the leaf would otherwise
    # pass here and fail only at delivery.
    creds = load_chat_credentials_from_env()

    assert creds.app_id == "cli_from_store"
    assert creds.app_secret == "s_from_store"
    assert creds.receive_id == "oc_from_store"
    assert creds.receive_id_type == "chat_id"
    assert creds.allowed_open_ids == "ou_from_store"

    loaded = load_feishu_gateway_settings()

    assert loaded.app_id == "cli_from_store"
    assert loaded.app_secret == "s_from_store"
    assert loaded.allowed_open_ids == ["ou_from_store"]


def _set_chat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "s_env")
    monkeypatch.setenv("FEISHU_CHAT_RECEIVE_ID", "oc_env")
    monkeypatch.setenv("FEISHU_ALLOWED_OPEN_IDS", "ou_env")


def _assert_env_policy_resolution(user_id: str, *, inbound_enabled: bool = True) -> None:
    store_path = resolve_store_path()
    before = store_path.read_bytes()
    effective = catalog.resolve_effective_integrations()["feishu"]
    creds = load_chat_credentials_from_env()
    loaded = load_feishu_gateway_settings()
    _, policy = load_identity_policy("feishu")

    assert effective["source"] == "local env"
    assert effective["config"]["app_id"] == creds.app_id == loaded.app_id == "cli_env"
    assert effective["config"]["app_secret"] == creds.app_secret == loaded.app_secret == "s_env"
    assert effective["config"]["receive_id"] == creds.receive_id == "oc_env"
    assert loaded.allowed_open_ids == ["ou_env"]
    assert policy.allowed_user_ids == [user_id]
    assert policy.inbound_enabled is inbound_enabled
    assert authorize_inbound_message(policy=policy, user_id=user_id).allowed is inbound_enabled
    assert (
        is_feedback_actor_authorized(
            open_id=user_id, chat_id="oc_env", env_allowed_open_ids=loaded.allowed_open_ids
        )
        is inbound_enabled
    )
    for denied in ("ou_env", "ou_stranger"):
        assert not authorize_inbound_message(policy=policy, user_id=denied).allowed
        assert not is_feedback_actor_authorized(
            open_id=denied, chat_id="oc_env", env_allowed_open_ids=loaded.allowed_open_ids
        )
    assert store_path.read_bytes() == before


def test_policy_only_store_and_env_resolve_through_gateway_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "integrations.json")
    _set_chat_env(monkeypatch)
    policy = MessagingIdentityPolicy(inbound_enabled=True, allowed_user_ids=["ou_policy"])
    save_identity_policy("feishu", None, policy)

    _assert_env_policy_resolution("ou_policy")

    record, policy = load_identity_policy("feishu")
    policy.inbound_enabled = False
    save_identity_policy("feishu", record, policy)
    _assert_env_policy_resolution("ou_policy", inbound_enabled=False)


def test_policy_only_resolution_respects_bound_org_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path / "opensre-home")
    monkeypatch.setattr("integrations.store.STORE_PATH", None)
    monkeypatch.delenv(INTEGRATIONS_STORE_PATH_ENV, raising=False)
    monkeypatch.delenv("OPENSRE_CONTEXT_ROOT", raising=False)
    _set_chat_env(monkeypatch)
    scope_before = current_scope()
    assert scope_before is None
    save_identity_policy(
        "feishu",
        None,
        MessagingIdentityPolicy(inbound_enabled=True, allowed_user_ids=["ou_unbound"]),
    )
    unbound_path = resolve_store_path()
    unbound_before = unbound_path.read_bytes()
    scopes = [
        StorageScope(principal=Principal.org("org_a"), actor=Actor("ou_a")),
        StorageScope(principal=Principal.org("org_b"), actor=Actor("ou_b")),
    ]
    snapshots: dict[Path, bytes] = {}
    for scope in scopes:
        with bound_storage_scope(scope):
            assert resolve_store_path() == (
                tmp_path / "opensre-home" / "orgs" / scope.principal.id / "integrations.json"
            )
            save_identity_policy(
                "feishu",
                None,
                MessagingIdentityPolicy(inbound_enabled=True, allowed_user_ids=[scope.actor.id]),
            )
            snapshots[resolve_store_path()] = resolve_store_path().read_bytes()
    for scope in scopes:
        with bound_storage_scope(scope):
            _assert_env_policy_resolution(scope.actor.id)
            _, policy = load_identity_policy("feishu")
            other = "ou_b" if scope.actor.id == "ou_a" else "ou_a"
            assert not authorize_inbound_message(policy=policy, user_id=other).allowed
            assert not authorize_inbound_message(policy=policy, user_id="ou_unbound").allowed
    assert current_scope() is scope_before
    assert resolve_store_path() == unbound_path
    assert unbound_path.read_bytes() == unbound_before
    _assert_env_policy_resolution("ou_unbound")
    assert all(path.read_bytes() == snapshot for path, snapshot in snapshots.items())
