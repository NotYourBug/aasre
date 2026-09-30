"""Derive channel facts solely from the frozen turn's surface and metadata."""

from collections.abc import Mapping

from infrastructure.harness_providers.messaging_sources import recognized_messaging_platform
from infrastructure.harness_providers.prompt_context import ActionPromptContext


def build_action_prompt_context(
    *,
    surface: str | None,
    resolved_integrations: Mapping[str, object],
    offered_tool_names: frozenset[str],
) -> ActionPromptContext:
    """Normalize explicit surface authority and injected gateway metadata."""
    gateway = surface == "gateway" or (
        surface is None
        and (
            "_gateway_platform" in resolved_integrations
            or "_gateway_chat_id" in resolved_integrations
        )
    )
    active = recognized_messaging_platform(resolved_integrations.get("_gateway_platform"))
    return ActionPromptContext(
        "gateway" if gateway else "interactive_shell",
        active if gateway else None,
        offered_tool_names,
    )
