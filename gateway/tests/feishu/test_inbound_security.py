from __future__ import annotations

from unittest.mock import patch

import pytest

from gateway.core.middleware.identity_policy import persist_policy_if_needed
from gateway.transports.feishu.inbound_security import (
    enforce_inbound_feishu_message_security,
    is_feedback_actor_authorized,
    is_open_id_authorized,
)
from integrations.messaging_security import MessagingIdentityPolicy

_SECURITY = "gateway.transports.feishu.inbound_security"


@pytest.mark.usefixtures("mock_integration_store")
def test_feedback_authorization_does_not_log_actor_metadata() -> None:
    with patch(f"{_SECURITY}.audit_log_inbound_message") as audit:
        assert is_feedback_actor_authorized(
            open_id="actor", chat_id="chat", env_allowed_open_ids=["actor"]
        )
        assert not is_feedback_actor_authorized(
            open_id="denied", chat_id="chat", env_allowed_open_ids=["actor"]
        )
        audit.assert_not_called()


def test_feedback_actor_must_remain_in_configured_allowlist() -> None:
    policy = MessagingIdentityPolicy(inbound_enabled=True, allowed_user_ids=["actor"])
    with patch(f"{_SECURITY}.load_identity_policy", return_value=(None, policy)):
        assert not is_feedback_actor_authorized(
            open_id="actor", chat_id="chat", env_allowed_open_ids=["someone_else"]
        )


@pytest.fixture
def mock_integration_store():
    with (
        patch("gateway.core.middleware.identity_policy.get_integration", return_value=None),
        patch("gateway.core.middleware.identity_policy.upsert_instance") as upsert,
    ):
        yield upsert


@pytest.mark.usefixtures("mock_integration_store")
def test_help_is_not_agent_turn() -> None:
    decision = enforce_inbound_feishu_message_security(
        user_id="ou_42",
        chat_id="oc_42",
        text="/help",
        env_allowed_open_ids=["ou_42"],
    )
    assert decision.allowed is False
    assert "OpenSRE Feishu gateway" in decision.reply_text


@pytest.mark.usefixtures("mock_integration_store")
def test_unauthorized_user_gets_reason() -> None:
    decision = enforce_inbound_feishu_message_security(
        user_id="ou_99",
        chat_id="oc_99",
        text="hello",
        env_allowed_open_ids=["ou_42"],
    )
    assert decision.allowed is False
    assert decision.reply_text


def test_pair_attempt_persists_policy(mock_integration_store: pytest.MonkeyPatch) -> None:
    policy = MessagingIdentityPolicy(
        inbound_enabled=True,
        pairing_secret_hash="abc",
    )
    with (
        patch(
            f"{_SECURITY}.load_identity_policy",
            return_value=(None, policy),
        ),
        patch(
            f"{_SECURITY}.complete_pairing",
            return_value=(True, "Pairing successful!"),
        ),
    ):
        decision = enforce_inbound_feishu_message_security(
            user_id="ou_42",
            chat_id="oc_42",
            text="/pair CODE",
            env_allowed_open_ids=[],
        )
    assert decision.persist_policy is True
    persist_policy_if_needed("feishu", decision)
    mock_integration_store.assert_called_once()


@pytest.mark.usefixtures("mock_integration_store")
def test_unauthorized_user_cannot_rotate_session() -> None:
    decision = enforce_inbound_feishu_message_security(
        user_id="ou_99",
        chat_id="oc_99",
        text="/new",
        env_allowed_open_ids=["ou_42"],
    )
    assert decision.allowed is False
    assert decision.reply_text
    assert decision.reply_text != "__ROTATE_SESSION__"


@pytest.mark.usefixtures("mock_integration_store")
def test_authorized_user_can_rotate_session() -> None:
    decision = enforce_inbound_feishu_message_security(
        user_id="ou_42",
        chat_id="oc_42",
        text="/new",
        env_allowed_open_ids=["ou_42"],
    )
    assert decision.allowed is True
    assert decision.reply_text == "__ROTATE_SESSION__"


@pytest.mark.usefixtures("mock_integration_store")
def test_is_open_id_authorized_reflects_the_allowlist() -> None:
    assert (
        is_open_id_authorized(open_id="ou_42", chat_id="oc_42", env_allowed_open_ids=["ou_42"])
        is True
    )
    assert (
        is_open_id_authorized(open_id="ou_99", chat_id="oc_42", env_allowed_open_ids=["ou_42"])
        is False
    )
