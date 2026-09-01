"""Feishu gateway configuration loaded from env."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.constants.feishu import FEISHU_APP_ID_ENV, FEISHU_APP_SECRET_ENV
from config.strict_config import StrictConfigModel
from gateway.core.lifecycle.errors import GatewayConfigurationError
from infrastructure.turn_host.concurrency import turn_limit_for_profile


class FeishuGatewaySettings(StrictConfigModel):
    """Runtime settings for the Feishu gateway worker."""

    app_id: str
    app_secret: str
    max_concurrent_turns: int = Field(default_factory=turn_limit_for_profile, ge=1)
    startup_timeout_seconds: float = Field(default=30.0, gt=0)
    turn_timeout_seconds: float = Field(default=240.0, gt=0)
    status_update_interval_seconds: float = Field(default=1.5, gt=0)


class FeishuGatewayEnv(BaseSettings):
    """Environment-backed Feishu gateway settings."""

    model_config = SettingsConfigDict(env_prefix="FEISHU_", extra="ignore")

    app_id: str = ""
    app_secret: str = ""
    gateway_turn_timeout_seconds: float = Field(default=240.0, gt=0)
    gateway_status_update_interval_seconds: float = Field(default=1.5, gt=0)


def load_feishu_gateway_settings() -> FeishuGatewaySettings:
    """Load Feishu gateway settings, raising when credentials are missing."""
    env = FeishuGatewayEnv()
    if not env.app_id or not env.app_secret:
        raise GatewayConfigurationError(
            f"Feishu app credentials missing. Set {FEISHU_APP_ID_ENV} and {FEISHU_APP_SECRET_ENV}."
        )
    return FeishuGatewaySettings(
        app_id=env.app_id,
        app_secret=env.app_secret,
        turn_timeout_seconds=env.gateway_turn_timeout_seconds,
        status_update_interval_seconds=env.gateway_status_update_interval_seconds,
    )
