"""Approved ACTION-surface Feishu message send."""

from __future__ import annotations

from typing import Any

from core.agent.cancel import tool_resources_cancel_requested
from core.domain.types.tools import ToolSurface
from core.tool import AgentToolContext, BaseTool, SideEffectLevel, ToolExecutionResult
from core.tool_framework import tool
from integrations.feishu.credentials import load_chat_credentials_from_env
from integrations.feishu.document_delivery import deliver_feishu_document
from integrations.feishu.outbound_targets import (
    has_outbound_target,
    parse_outbound_targets,
    reauthorize_approved_target,
)
from integrations.feishu.tools.feishu_send_message_tool.validation import prepare_send_input
from integrations.feishu.tools.results import delivery_result, failed_result


class FeishuSendMessageTool(BaseTool):
    """Send complete Markdown to one approved exact Feishu destination."""

    name = "feishu_send_message"
    description = "Send a Markdown message to an authorized Feishu target after approval."
    source = "feishu"
    requires = ["feishu"]
    surfaces = (ToolSurface.ACTION,)
    side_effect_level = SideEffectLevel.EXTERNAL
    requires_approval = True
    approval_reason = "Sends a Feishu message to the displayed exact target."
    parallel_safe = False
    accepts_runtime_context = True
    log_omitted_input_fields = ("message", "target")
    input_schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "minLength": 1,
                "description": "current, default, or an exact allowed receive_id_type:receive_id target.",
            },
            "message": {
                "type": "string",
                "minLength": 1,
                "description": "Complete Markdown body; no truncation.",
            },
        },
        "required": ["target", "message"],
        "additionalProperties": False,
    }

    def is_available(self, sources: dict[str, dict]) -> bool:
        return has_outbound_target(sources)

    def prepare_public_input(
        self, payload: dict[str, Any], resolved_integrations: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        return prepare_send_input(payload, resolved_integrations)

    def run(self, target: str, message: str, *, context: AgentToolContext) -> ToolExecutionResult:
        if not target.strip() or not message.strip():
            return failed_result(
                target=target,
                error_type="validation",
                error="Feishu message request is invalid",
            )
        if tool_resources_cancel_requested(context.resources):
            return failed_result(
                target=target,
                error_type="cancelled",
                error="Feishu delivery was cancelled",
            )
        try:
            credentials = load_chat_credentials_from_env()
            if not credentials.app_id.strip() or not credentials.app_secret.strip():
                raise ValueError("missing credentials")
            resolved = reauthorize_approved_target(
                target,
                default_receive_id_type=credentials.receive_id_type,
                default_receive_id=credentials.receive_id,
                allowed_targets=parse_outbound_targets(credentials.allowed_outbound_targets),
                gateway_platform=str(context.resolved_integrations.get("_gateway_platform") or ""),
                gateway_chat_id=str(context.resolved_integrations.get("_gateway_chat_id") or ""),
            )
        except (ValueError, PermissionError):
            return failed_result(
                target=target,
                error_type="authorization",
                error="Feishu target is no longer authorized",
            )

        try:
            delivered = deliver_feishu_document(
                app_id=credentials.app_id,
                app_secret=credentials.app_secret,
                receive_id=resolved.receive_id,
                receive_id_type=resolved.receive_id_type,
                markdown=message,
                cancel_requested=lambda: tool_resources_cancel_requested(context.resources),
                stop_on_ambiguous_write=True,
            )
        except Exception:
            return failed_result(
                target=target,
                error_type="delivery_uncertain",
                error="Feishu delivery could not be confirmed",
                attempted=True,
                certainty="maybe_sent",
            )
        return delivery_result(delivered, target=resolved.canonical)


feishu_send_message = tool(FeishuSendMessageTool(), surfaces=(ToolSurface.ACTION,))

__all__ = ["FeishuSendMessageTool", "feishu_send_message"]
