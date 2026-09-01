"""Tests for tools.system.watch_dog.runner._build_dispatcher provider routing."""

from __future__ import annotations

import pytest

from integrations.feishu.alarms import FeishuAlarmCredentials, FeishuAlarmDispatcher
from integrations.rocketchat.alarms import RocketChatAlarmDispatcher
from integrations.rocketchat.credentials import RocketChatCredentials
from integrations.telegram.alarms import AlarmDispatcher
from integrations.telegram.credentials import TelegramCredentials
from tools.system.watch_dog.config import WatchdogConfig
from tools.system.watch_dog.runner import _build_dispatcher


def test_defaults_to_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools.system.watch_dog.runner.load_credentials_from_env",
        lambda **_kw: TelegramCredentials(bot_token="tok", chat_id="chat-1"),
    )
    config = WatchdogConfig(pid=1, max_cpu=80.0, chat_id="chat-1")

    dispatcher = _build_dispatcher(config)

    assert isinstance(dispatcher, AlarmDispatcher)


def test_rocketchat_provider_builds_rocketchat_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tools.system.watch_dog.runner.load_rocketchat_credentials_from_env",
        lambda **_kw: RocketChatCredentials(
            server_url="https://chat.example.com",
            auth_token="tok",
            user_id="u1",
            channel="#incidents",
        ),
    )
    config = WatchdogConfig(pid=1, max_cpu=80.0, provider="rocketchat", chat_id="#incidents")

    dispatcher = _build_dispatcher(config)

    assert isinstance(dispatcher, RocketChatAlarmDispatcher)


def test_rocketchat_provider_passes_chat_id_as_channel_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_load(**kwargs: object) -> RocketChatCredentials:
        captured.update(kwargs)
        return RocketChatCredentials(
            server_url="https://chat.example.com",
            auth_token="tok",
            user_id="u1",
            channel="#ops",
        )

    monkeypatch.setattr(
        "tools.system.watch_dog.runner.load_rocketchat_credentials_from_env",
        _fake_load,
    )
    config = WatchdogConfig(pid=1, max_cpu=80.0, provider="rocketchat", chat_id="#ops")

    _build_dispatcher(config)

    assert captured == {"channel_override": "#ops"}


def test_feishu_provider_builds_feishu_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_load_feishu_credentials(**kwargs: object) -> FeishuAlarmCredentials:
        return FeishuAlarmCredentials(app_id="cli_x", app_secret="s_x", receive_id="oc_x")

    monkeypatch.setattr(
        "tools.system.watch_dog.runner.load_feishu_credentials_from_env",
        _fake_load_feishu_credentials,
    )
    config = WatchdogConfig(pid=1, max_cpu=80.0, provider="feishu", chat_id="oc_ops")

    dispatcher = _build_dispatcher(config)

    assert isinstance(dispatcher, FeishuAlarmDispatcher)


def test_feishu_provider_passes_chat_id_as_channel_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_load_feishu_credentials(**kwargs: object) -> FeishuAlarmCredentials:
        captured.update(kwargs)
        return FeishuAlarmCredentials(app_id="cli_x", app_secret="s_x", receive_id="oc_x")

    monkeypatch.setattr(
        "tools.system.watch_dog.runner.load_feishu_credentials_from_env",
        _fake_load_feishu_credentials,
    )
    config = WatchdogConfig(pid=1, max_cpu=80.0, provider="feishu", chat_id="oc_ops")

    _build_dispatcher(config)

    assert captured == {"channel_override": "oc_ops"}
