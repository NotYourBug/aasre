"""Feishu extra-write recipes for the exact offered send/reply subset."""

from infrastructure.harness_providers.prompt_context import ActionPromptContext


def feishu_action_prompt_fragment(context: ActionPromptContext) -> str:
    """Emit eligible Feishu write guidance without adding read capabilities."""
    if context.surface == "gateway" and context.active_platform != "feishu":
        return ""
    recipes: list[str] = []
    if "feishu_send_message" in context.offered_tool_names:
        recipes.append(
            "feishu_send_message: use only for an explicit extra approved send. "
            "Use target=current for the frozen current chat when applicable; other targets "
            "must pass the tool's existing authorization. Each call requires approval."
        )
    if "feishu_reply_message" in context.offered_tool_names:
        recipes.append(
            "feishu_reply_message: an explicit extra approved reply requires a known message_id. "
            "Never invent a parent message. The tool checks the parent's chat before writing; "
            "each call requires approval. Use its thread option only when requested."
        )
    if not recipes:
        return ""
    return "\n".join(
        [
            "FEISHU ADDITIONAL DELIVERY: Ordinary answers go through gateway output automatically; "
            "never call an extra write tool to duplicate them. Partial or maybe_sent results "
            "must not be retried automatically.",
            *recipes,
        ]
    )
