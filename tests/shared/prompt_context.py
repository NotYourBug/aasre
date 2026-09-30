"""Explicit offered-tool fixtures for legacy shell prompt contracts."""

from infrastructure.harness_providers.prompt_context import ActionPromptContext

SHELL_PROMPT_CONTEXT = ActionPromptContext(
    "interactive_shell",
    None,
    frozenset(
        {
            "skill_view",
            "investigation_start",
            "slash_invoke",
            "shell_run",
            "cli_exec",
            "slack_send_message",
            "slack_reply_message",
            "slack_read_messages",
            "slack_search_messages",
            "slack_list_team_members",
            "slack_join_channel",
            "slack_add_reaction",
            "github_cli",
            "get_github_star_history",
            "telegram_send_message",
            "rocketchat_send_message",
            "buzz_send_message",
            "feishu_send_message",
            "feishu_reply_message",
            "work_task_add",
            "work_task_complete",
            "work_task_prioritize",
        }
    ),
)

SLACK_PROMPT_CONTEXT = ActionPromptContext(
    "gateway", "slack", SHELL_PROMPT_CONTEXT.offered_tool_names
)
