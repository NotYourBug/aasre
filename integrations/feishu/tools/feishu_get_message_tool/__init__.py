"""Registry entrypoint for current-chat Feishu reads."""

from integrations.feishu.tools.feishu_get_message_tool.tool import feishu_get_message

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "feishu_get_message"]
