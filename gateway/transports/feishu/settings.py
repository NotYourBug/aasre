"""Feishu gateway configuration loaded from env."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from config.constants.feishu import (
    FEISHU_ALLOWED_OPEN_IDS_ENV,
    FEISHU_APP_ID_ENV,
    FEISHU_APP_SECRET_ENV,
)
from config.strict_config import StrictConfigModel
from gateway.core.lifecycle.errors import GatewayConfigurationError
from infrastructure.turn_host.concurrency import turn_limit_for_profile

logger = logging.getLogger(__name__)


class FeishuGatewaySettings(StrictConfigModel):
    """Runtime settings for the Feishu gateway worker."""

    app_id: str
    app_secret: str
    allowed_open_ids: list[str] = Field(default_factory=list)
    max_concurrent_turns: int = Field(default_factory=turn_limit_for_profile, ge=1)
    startup_timeout_seconds: float = Field(default=30.0, gt=0)
    turn_timeout_seconds: float = Field(default=240.0, gt=0)
    status_update_interval_seconds: float = Field(default=1.5, gt=0)


class FeishuGatewayEnv(BaseSettings):
    """Environment-backed Feishu gateway settings."""

    model_config = SettingsConfigDict(env_prefix="FEISHU_", extra="ignore")

    app_id: str = ""
    app_secret: str = ""
    # NoDecode keeps pydantic-settings from JSON-decoding the env value so the
    # CSV validator below can parse "ou_a,ou_b" instead of raising a SettingsError.
    allowed_open_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)
    gateway_turn_timeout_seconds: float = Field(default=240.0, gt=0)
    gateway_status_update_interval_seconds: float = Field(default=1.5, gt=0)

    @field_validator("allowed_open_ids", mode="before")
    @classmethod
    def parse_allowed_open_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


def load_feishu_gateway_settings() -> FeishuGatewaySettings:
    """Load Feishu gateway settings, raising when credentials are missing."""
    env = FeishuGatewayEnv()
    if not env.app_id or not env.app_secret:
        raise GatewayConfigurationError(
            f"Feishu app credentials missing. Set {FEISHU_APP_ID_ENV} and {FEISHU_APP_SECRET_ENV}."
        )
    if not env.allowed_open_ids:
        logger.warning(
            "Feishu allowed open_ids are not configured: inbound is deny-all until a "
            "user pairs via /pair or you set %s (comma-separated open_ids).",
            FEISHU_ALLOWED_OPEN_IDS_ENV,
        )
    return FeishuGatewaySettings(
        app_id=env.app_id,
        app_secret=env.app_secret,
        allowed_open_ids=env.allowed_open_ids,
        turn_timeout_seconds=env.gateway_turn_timeout_seconds,
        status_update_interval_seconds=env.gateway_status_update_interval_seconds,
    )
