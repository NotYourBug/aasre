"""Current-chat read authority from immutable turn facts."""

from collections.abc import Mapping
from typing import Any

from core.tool import availability_view
from infrastructure.harness_providers.prompt_context import (
    ACTION_PROMPT_CONTEXT_RESOURCE,
    ActionPromptContext,
)
from integrations.config_models import FeishuConfig
from integrations.feishu.read_types import FeishuReadError, FeishuReadErrorCode, FeishuReadScope


def _valid_chat_app(config: object) -> FeishuConfig | None:
    if not isinstance(config, dict) or config.get("connection_verified") is not True:
        return None
    for field in ("app_id", "app_secret"):
        value = config.get(field)
        if not isinstance(value, str) or not value.strip():
            return None
    try:
        return FeishuConfig.model_validate(
            {key: config[key] for key in FeishuConfig.model_fields if key in config}
        )
    except (ValueError, TypeError):
        return None


def resolve_read_scope(resolved_integrations: dict[str, Any]) -> FeishuReadScope:
    """Capture validated application and current chat without consulting write targets."""
    view = availability_view(resolved_integrations)
    chat_id = view.get("_gateway_chat_id")
    config = _valid_chat_app(view.get("feishu"))
    if (
        view.get("_gateway_platform") != "feishu"
        or not isinstance(chat_id, str)
        or not chat_id.strip()
        or config is None
    ):
        raise FeishuReadError(FeishuReadErrorCode.AUTHORIZATION)
    return FeishuReadScope(config.app_id, chat_id.strip())


def is_read_available(sources: dict[str, Any]) -> bool:
    """Check integration-scope candidacy without credential loading or network I/O."""
    try:
        resolve_read_scope(sources)
    except FeishuReadError:
        return False
    return True


def resolve_runtime_read_scope(
    resolved_integrations: dict[str, Any], resources: Mapping[str, Any]
) -> FeishuReadScope:
    """Require the frozen runtime host to be a Feishu gateway before resolving scope."""
    prompt = resources.get(ACTION_PROMPT_CONTEXT_RESOURCE)
    if (
        not isinstance(prompt, ActionPromptContext)
        or prompt.surface != "gateway"
        or prompt.active_platform != "feishu"
    ):
        raise FeishuReadError(FeishuReadErrorCode.AUTHORIZATION)
    return resolve_read_scope(resolved_integrations)
