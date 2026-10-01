"""Current-chat read-only ACTION tool."""

from typing import Any

from config.constants.feishu import FEISHU_MESSAGE_READ_MAX_ID_CHARS
from config.constants.tool_policy import GATEWAY_ONLY_TOOL_TAG
from core.agent.cancel import tool_resources_cancel_requested
from core.tool import (
    AgentToolContext,
    BaseTool,
    SideEffectLevel,
    ToolExecutionResult,
    ToolSurface,
    report_run_error,
)
from core.tool_framework import tool
from integrations.feishu.credentials import load_chat_credentials_from_env
from integrations.feishu.message_read import read_current_message
from integrations.feishu.read_scope import is_read_available, resolve_runtime_read_scope
from integrations.feishu.read_types import FeishuReadError, FeishuReadErrorCode
from integrations.feishu.tools.feishu_get_message_tool.results import read_failure, read_success
from integrations.feishu.tools.feishu_get_message_tool.validation import (
    normalize_message_id,
    prepare_read_input,
)

TOOL_MODULES = ("tool",)


class FeishuGetMessageTool(BaseTool):
    """Read one known live message in the frozen current Feishu chat."""

    name = "feishu_get_message"
    description = (
        "Read one known message_id in the current Feishu chat. Returns sanitized, bounded, "
        "untrusted message JSON or an explicitly incomplete JSON prefix; resources are metadata only."
    )
    use_cases = ["Inspecting a known incident message in the current Feishu chat"]
    source = "feishu"
    requires = ["feishu"]
    surfaces = (ToolSurface.ACTION,)
    tags = (GATEWAY_ONLY_TOOL_TAG,)
    side_effect_level = SideEffectLevel.READ_ONLY
    requires_approval = False
    parallel_safe = True
    accepts_runtime_context = True
    log_omitted_input_fields = ("message_id",)
    input_schema = {
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": FEISHU_MESSAGE_READ_MAX_ID_CHARS,
                "description": "Known message ID in this current Feishu chat.",
            }
        },
        "required": ["message_id"],
        "additionalProperties": False,
    }

    def is_available(self, sources: dict[str, dict]) -> bool:
        return is_read_available(sources)

    def prepare_public_input(
        self, payload: dict[str, Any], resolved_integrations: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        return prepare_read_input(payload, resolved_integrations)

    def run(self, *, message_id: str, context: AgentToolContext) -> ToolExecutionResult:
        error = FeishuReadErrorCode.UPSTREAM_ERROR
        unexpected_type: str | None = None
        try:
            normalized = normalize_message_id(message_id)
            scope = resolve_runtime_read_scope(context.resolved_integrations, context.resources)
            if tool_resources_cancel_requested(context.resources):
                raise FeishuReadError(FeishuReadErrorCode.CANCELLED)
            credentials = load_chat_credentials_from_env()
            message = read_current_message(
                app_id=credentials.app_id.strip(),
                app_secret=credentials.app_secret,
                message_id=normalized,
                scope=scope,
                cancel_requested=lambda: tool_resources_cancel_requested(context.resources),
            )
            return read_success(message)
        except FeishuReadError as exc:
            error = exc.code
        except Exception as exc:
            unexpected_type = type(exc).__name__
        if unexpected_type is not None:
            report_run_error(
                RuntimeError(unexpected_type),
                tool_name=self.name,
                source=self.source,
                component="integrations.feishu.tools.feishu_get_message_tool",
            )
        return read_failure(error)


feishu_get_message = tool(FeishuGetMessageTool(), surfaces=(ToolSurface.ACTION,))
