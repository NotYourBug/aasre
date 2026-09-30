"""Registry entrypoint for the Feishu send-message tool."""

from integrations.feishu.tools.feishu_send_message_tool.tool import (
    FeishuSendMessageTool,
    feishu_send_message,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "FeishuSendMessageTool", "feishu_send_message"]
