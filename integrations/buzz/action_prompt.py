"""BUZZ delivery recipes for the final offered tool view."""

from infrastructure.harness_providers.prompt_context import ActionPromptContext


def buzz_action_prompt_fragment(context: ActionPromptContext) -> str:
    """Emit BUZZ delivery guidance only on an eligible surface."""
    if context.surface == "gateway" and context.active_platform != "buzz":
        return ""
    if "buzz_send_message" not in context.offered_tool_names:
        return ""
    lines = [
        "BUZZ DELIVERY:",
        "buzz_send_message — send only when the user explicitly asks for an extra "
        + "BUZZ send, post, notification or message. Use message and a named channel UUID; omit channel to use the configured default_channel.",
        "The gateway delivers ordinary answers automatically. Do not duplicate them with "
        + "an extra send. Do not invent delivery commands or substitute another channel.",
    ]
    if context.surface == "interactive_shell" and "slash_invoke" in context.offered_tool_names:
        lines.append('For setup use slash_invoke(command="/integrations", args=["setup", "buzz"]).')
    return "\n".join(lines)


__all__ = ["buzz_action_prompt_fragment"]
