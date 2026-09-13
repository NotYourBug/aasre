"""Feishu credential classification for the integration catalog."""

from __future__ import annotations

from integrations.feishu.classify import classify


def test_returns_none_without_an_app_id() -> None:
    """A record with no chat-app app id is not a Feishu chat integration."""
    assert classify({"app_secret": "s"}, "env:feishu") == (None, None)


def test_returns_none_for_an_alert_push_only_record() -> None:
    """ALERTPUSH_* credentials belong to the separate one-way app, not the chat app."""
    assert classify({"app_secret": "s", "receive_id": "oc_1"}, "env:feishu") == (None, None)


def test_classifies_chat_app_credentials() -> None:
    config, service = classify(
        {
            "app_id": "cli_1",
            "app_secret": "s",
            "receive_id": "oc_1",
            "receive_id_type": "chat_id",
            "allowed_open_ids": "ou_1,ou_2",
        },
        "env:feishu",
    )

    assert service == "feishu"
    assert config == {
        "app_id": "cli_1",
        "app_secret": "s",
        "receive_id": "oc_1",
        "receive_id_type": "chat_id",
        "allowed_open_ids": "ou_1,ou_2",
    }


def test_defaults_receive_id_type_to_chat_id() -> None:
    """A record that never set a type delivers to a group, not a person."""
    config, _service = classify({"app_id": "cli_1"}, "env:feishu")

    assert config is not None
    assert config["receive_id_type"] == "chat_id"


def test_rejects_a_whitespace_only_app_id() -> None:
    """A blank app id is refused before the config model is built."""
    assert classify({"app_id": "   "}, "env:feishu") == (None, None)
