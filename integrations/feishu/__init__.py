"""Feishu integration: alarm delivery for the watchdog."""

from integrations.feishu.alarms import FeishuAlarmDispatcher, load_credentials_from_env

__all__ = ["FeishuAlarmDispatcher", "load_credentials_from_env"]
