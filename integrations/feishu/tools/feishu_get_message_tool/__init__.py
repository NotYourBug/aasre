"""Registry entrypoint for current-chat Feishu reads."""

from integrations.feishu.tools.feishu_get_message_tool.tool import feishu_get_message

__all__ = ["feishu_get_message"]
