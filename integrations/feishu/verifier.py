"""Feishu integration verifier — chat-app tenant-token probe.

Uses the same ``lark.Client`` builder + :class:`TokenManager` path the chat
transport uses to verify credentials at startup (``gateway/transports/feishu/
worker.py::_verify_feishu_credentials``), so a passing verification exercises
the exact credential path turns take.
"""

from __future__ import annotations

import logging
from typing import Any

from infrastructure.delivery.notifications.redaction import redact_token
from integrations.verification import register_verifier, result

logger = logging.getLogger(__name__)


@register_verifier("feishu")
def verify_feishu(source: str, config: dict[str, Any]) -> dict[str, str]:
    """Probe the chat-app credentials by fetching a tenant access token."""
    app_id = str(config.get("app_id", "")).strip()
    app_secret = str(config.get("app_secret", "")).strip()
    if not app_id or not app_secret:
        return result("feishu", source, "missing", "Missing app_id or app_secret.")

    try:
        import lark_oapi as lark
        from lark_oapi.core.token import TokenManager

        client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        TokenManager.get_self_tenant_token(client.config)
    except Exception as exc:
        # The SDK's auth error text includes the credentials that failed, so
        # the detail is redacted before it reaches verify output.
        safe_error = redact_token(str(exc), app_secret)
        return result("feishu", source, "failed", f"Feishu credential check failed: {safe_error}")

    return result("feishu", source, "passed", "Connected to the Feishu chat app.")


__all__ = ["verify_feishu"]
