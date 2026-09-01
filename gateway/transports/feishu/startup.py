"""Start the Feishu chat worker for the gateway."""

from __future__ import annotations

import logging

from gateway.core.lifecycle.errors import GatewayTransportFailedError
from gateway.transports.feishu.background import (
    FeishuGatewayBackground,
    start_feishu_gateway_background,
)
from gateway.transports.feishu.settings import (
    FeishuGatewaySettings,
    load_feishu_gateway_settings,
)
from infrastructure.turn_host.turn_callback import TurnCallback


def start_feishu_worker(
    *,
    logger: logging.Logger,
    handler: TurnCallback,
) -> tuple[FeishuGatewayBackground, FeishuGatewaySettings]:
    """Load Feishu settings and start the WebSocket background worker.

    Waits until the gateway is ready so the uniform transport registry can treat
    Feishu like Telegram/Slack/Discord (start returns only a live worker).
    """
    settings = load_feishu_gateway_settings()
    worker = start_feishu_gateway_background(
        settings=settings,
        logger=logger,
        handler=handler,
    )
    if not worker.wait_until_ready(timeout=settings.startup_timeout_seconds):
        logger.warning(
            "Feishu gateway did not become ready within %.0fs",
            settings.startup_timeout_seconds,
        )
        worker.stop()
        raise GatewayTransportFailedError("startup timeout")
    return worker, settings


__all__ = ["start_feishu_worker"]
