"""ROCKET.CHAT delivery recipes for the final offered tool view."""

from infrastructure.harness_providers.prompt_context import ActionPromptContext


def rocketchat_action_prompt_fragment(context: ActionPromptContext) -> str:
    """Emit ROCKET.CHAT delivery guidance only on an eligible surface."""
    if context.surface == "gateway" and context.active_platform != "rocketchat":
        return ""
    if "rocketchat_send_message" not in context.offered_tool_names:
        return ""
    lines = [
        "ROCKET.CHAT DELIVERY:",
        "rocketchat_send_message — send only when the user explicitly asks for an extra "
        + "ROCKET.CHAT send, post, notification or message. Use message and the named channel (#channel / @user); with a fixed webhook omit channel.",
        "The gateway delivers ordinary answers automatically. Do not duplicate them with "
        + "an extra send. Do not invent delivery commands or substitute another channel.",
    ]
    if context.surface == "interactive_shell" and "slash_invoke" in context.offered_tool_names:
        lines.append(
            'For setup use slash_invoke(command="/integrations", args=["setup", "rocketchat"]).'
        )
    return "\n".join(lines)


__all__ = ["rocketchat_action_prompt_fragment"]
