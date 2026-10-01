"""Current-chat bounded search ACTION tool."""

import time
from typing import Any

from config.constants.feishu import (
    FEISHU_SEARCH_DEFAULT_LIMIT,
    FEISHU_SEARCH_MAX_QUERY_CHARS,
    FEISHU_SEARCH_MAX_RESULTS,
)
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
from integrations.feishu.message_search import search_current_messages
from integrations.feishu.read_scope import is_read_available, resolve_runtime_read_scope
from integrations.feishu.read_types import FeishuReadError
from integrations.feishu.search_input import normalize_search_input
from integrations.feishu.search_types import FeishuSearchError, FeishuSearchErrorCode
from integrations.feishu.tools.feishu_search_messages_tool.results import (
    search_failure,
    search_success,
)
from integrations.feishu.tools.feishu_search_messages_tool.validation import prepare_search_input

TOOL_MODULES = ("tool",)


class FeishuSearchMessagesTool(BaseTool):
    """Search bounded history in the frozen current Feishu chat."""

    name = "feishu_search_messages"
    description = (
        "Find message IDs and sanitized untrusted previews in a declared current-chat time window. "
        "Literal case-insensitive matching scans at most 150 messages in a window of at most 7 days; "
        "empty query discovers recent IDs. Completeness applies only to the declared chat container."
    )
    use_cases = ["Finding incident evidence or discovering message IDs in the current Feishu chat"]
    source = "feishu"
    requires = ["feishu"]
    surfaces = (ToolSurface.ACTION,)
    tags = (GATEWAY_ONLY_TOOL_TAG,)
    side_effect_level = SideEffectLevel.READ_ONLY
    requires_approval = False
    parallel_safe = True
    accepts_runtime_context = True
    log_omitted_input_fields = ("query", "start_time", "end_time")
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": FEISHU_SEARCH_MAX_QUERY_CHARS,
                "description": "Literal keyword; empty discovers recent message IDs.",
            },
            "start_time": {
                "type": "integer",
                "minimum": 0,
                "description": "Start of an explicit window in Unix seconds; at most 7 days.",
            },
            "end_time": {
                "type": "integer",
                "minimum": 0,
                "description": "End in Unix seconds, after start and no later than now.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": FEISHU_SEARCH_MAX_RESULTS,
                "default": FEISHU_SEARCH_DEFAULT_LIMIT,
                "description": "Maximum returned items; scanning remains bounded.",
            },
        },
        "required": ["start_time", "end_time"],
        "additionalProperties": False,
    }

    def is_available(self, sources: dict[str, dict]) -> bool:
        return is_read_available(sources)

    def prepare_public_input(
        self, payload: dict[str, Any], resolved_integrations: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        return prepare_search_input(payload, resolved_integrations)

    def run(
        self,
        *,
        start_time: int,
        end_time: int,
        query: str = "",
        limit: int = FEISHU_SEARCH_DEFAULT_LIMIT,
        context: AgentToolContext,
    ) -> ToolExecutionResult:
        error = FeishuSearchErrorCode.UPSTREAM_ERROR
        unexpected_type: str | None = None
        try:
            inputs = normalize_search_input(
                start_time=start_time,
                end_time=end_time,
                query=query,
                limit=limit,
                now=int(time.time()),
            )
            scope = resolve_runtime_read_scope(context.resolved_integrations, context.resources)
            if tool_resources_cancel_requested(context.resources):
                raise FeishuSearchError(FeishuSearchErrorCode.CANCELLED)
            credentials = load_chat_credentials_from_env()
            result = search_current_messages(
                app_id=credentials.app_id.strip(),
                app_secret=credentials.app_secret,
                scope=scope,
                inputs=inputs,
                cancel_requested=lambda: tool_resources_cancel_requested(context.resources),
            )
            encoded = search_success(result)
            if tool_resources_cancel_requested(context.resources):
                raise FeishuSearchError(FeishuSearchErrorCode.CANCELLED)
            return encoded
        except FeishuSearchError as exc:
            error = exc.code
        except FeishuReadError:
            error = FeishuSearchErrorCode.AUTHORIZATION
        except Exception as exc:
            unexpected_type = type(exc).__name__
        if unexpected_type is not None:
            report_run_error(
                RuntimeError(unexpected_type),
                tool_name=self.name,
                source=self.source,
                component="integrations.feishu.tools.feishu_search_messages_tool",
            )
        return search_failure(error)


feishu_search_messages = tool(FeishuSearchMessagesTool(), surfaces=(ToolSurface.ACTION,))
