"""Slack action recipes selected from the final offered tool names."""

from infrastructure.harness_providers.prompt_context import ActionPromptContext

_RECIPES: tuple[tuple[frozenset[str], str], ...] = (
    (
        frozenset({"slack_send_message"}),
        """slack_send_message — send an explicitly requested notification through the incoming
webhook's fixed channel. Put the requested text in message. Observe any preceding
lookup before composing the actual message; never fabricate its values.""",
    ),
    (
        frozenset({"slack_reply_message"}),
        """slack_reply_message — post an explicitly requested message to a specific
channel_id (C… or #name), with optional thread_ts for a known thread.""",
    ),
    (
        frozenset({"slack_read_messages"}),
        """slack_read_messages — read conversation history in a channel or known thread.
Example: read the last 10 messages →
slack_read_messages(channel="#opensre-slack-testing", limit=10).
Use trusted current-chat tool context for an implicit target; do not invent IDs.
History is for what was said, not for workspace membership.""",
    ),
    (
        frozenset({"slack_search_messages"}),
        """slack_search_messages — search workspace messages using Slack search syntax.
Example: slack_search_messages(query="deploy freeze").""",
    ),
    (
        frozenset({"slack_list_team_members"}),
        """slack_list_team_members — workspace roster / people questions ignore channel_id
and call slack_list_team_members ONLY. Bot tools resolve their own credentials.
Roster follow-up: after "Want me to: offering more Slack roster/detail" and a
user's "yes", call slack_list_team_members and summarize the observed result.""",
    ),
    (
        frozenset({"slack_list_team_members", "slack_read_messages"}),
        "For roster questions, never slack_read_messages; use the roster tool.",
    ),
    (
        frozenset({"slack_join_channel"}),
        "slack_join_channel — join an explicitly named public channel.",
    ),
    (
        frozenset({"slack_add_reaction"}),
        "slack_add_reaction — add an explicitly requested emoji to a known message ts.",
    ),
    (
        frozenset({"slack_send_message", "slack_reply_message"}),
        "Prefer slack_reply_message for a named channel/thread; slack_send_message uses a fixed webhook.",
    ),
    (
        frozenset({"work_task_add"}),
        "work_task_add — capture an explicitly requested durable task; current-channel defaults use frozen tool context.",
    ),
    (
        frozenset({"work_task_complete"}),
        "work_task_complete — complete a known durable task when requested.",
    ),
    (
        frozenset({"work_task_prioritize"}),
        "work_task_prioritize — prioritize known tasks when requested.",
    ),
)


def slack_action_prompt_fragment(context: ActionPromptContext) -> str:
    """Emit only eligible Slack recipes on the shell or active Slack gateway."""
    if context.surface == "gateway" and context.active_platform != "slack":
        return ""
    messaging = {
        "slack_send_message",
        "slack_reply_message",
        "slack_read_messages",
        "slack_search_messages",
        "slack_list_team_members",
        "slack_join_channel",
        "slack_add_reaction",
    }
    if not messaging.intersection(context.offered_tool_names):
        return ""
    recipes = [
        text for requirements, text in _RECIPES if requirements <= context.offered_tool_names
    ]
    guidance = [
        "SLACK TEAMMATE REQUESTS USE SLACK TOOLS:",
        *recipes,
        "After each tool returns, answer from its output. Ordinary gateway answers are "
        "delivered automatically; extra delivery is for an explicit additional send or reply.",
        "Do NOT invent a delivery command: `/messaging send slack …` is NOT a real command. "
        "Do not substitute a different channel when the requested tool is unavailable.",
    ]
    if context.surface == "interactive_shell" and "slash_invoke" in context.offered_tool_names:
        guidance.append(
            'For setup use slash_invoke(command="/integrations", args=["setup", "slack"]).'
        )
    return "\n\n".join(guidance)


__all__ = ["slack_action_prompt_fragment"]
