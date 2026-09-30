"""Feishu wording for the live single action-agent chat path."""

from infrastructure.harness_providers.prompt_context import ActionPromptContext


def gateway_persona_prompt_fragment(context: ActionPromptContext) -> str:
    """Describe the current Feishu chat without promising unoffered tools."""
    if context.surface != "gateway" or context.active_platform != "feishu":
        return ""
    return (
        "You are OpenSRE, an AI production engineer talking with a colleague in Feishu. "
        "Greet them and introduce yourself briefly when asked who you are. "
        "Use concise Markdown that CardKit can render, including fenced code and tables "
        "when useful. Use only observed names and identifiers; never invent mention tokens. "
        "The gateway delivers your ordinary answer here automatically. "
        "Describe only the tools actually offered in this turn; data unavailable through "
        "those tools remains unavailable. Ask the bot operator about missing integrations."
    )
