"""Registry entrypoint for the Feishu reply-message tool."""

from integrations.feishu.tools.feishu_reply_message_tool.tool import (
    FeishuReplyMessageTool,
    feishu_reply_message,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "FeishuReplyMessageTool", "feishu_reply_message"]
