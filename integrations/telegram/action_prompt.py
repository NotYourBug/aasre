"""TELEGRAM delivery recipes for the final offered tool view."""

from infrastructure.harness_providers.prompt_context import ActionPromptContext

_DELIVERY_GUIDANCE = (
    "TELEGRAM DELIVERY:\n"
    "telegram_send_message — send only when the user explicitly asks for an extra "
    "TELEGRAM send, post, notification or message. Use the requested message body as message. Do not deliver generic alerts or investigations unless explicitly requested.\n"
    "The gateway delivers ordinary answers automatically. Do not duplicate them with "
    "an extra send. Do not invent delivery commands or substitute another channel."
)


def telegram_action_prompt_fragment(context: ActionPromptContext) -> str:
    """Emit TELEGRAM delivery guidance only on an eligible surface."""
    if context.surface == "gateway" and context.active_platform != "telegram":
        return ""
    if "telegram_send_message" not in context.offered_tool_names:
        return ""
    lines = [_DELIVERY_GUIDANCE]
    if context.surface == "interactive_shell" and "slash_invoke" in context.offered_tool_names:
        lines.append(
            'For setup use slash_invoke(command="/integrations", args=["setup", "telegram"]).'
        )
    return "\n".join(lines)


__all__ = ["telegram_action_prompt_fragment"]
