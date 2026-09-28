"""Approved ACTION-surface Feishu message reply."""

from __future__ import annotations

from typing import Any

from core.agent.cancel import tool_resources_cancel_requested
from core.domain.types.tools import ToolSurface
from core.tool import AgentToolContext, BaseTool, SideEffectLevel, ToolExecutionResult
from core.tool_framework import tool
from integrations.feishu.credentials import load_chat_credentials_from_env
from integrations.feishu.document_delivery import deliver_feishu_document
from integrations.feishu.message_lookup import lookup_reply_parent
from integrations.feishu.outbound_targets import (
    has_outbound_target,
    parse_outbound_targets,
    reauthorize_approved_target,
)
from integrations.feishu.tools.feishu_reply_message_tool.validation import prepare_reply_input
from integrations.feishu.tools.results import delivery_result, failed_result


class FeishuReplyMessageTool(BaseTool):
    """Reply with complete Markdown only to a verified parent in an approved chat."""

    name = "feishu_reply_message"
    description = "Reply to a message in an authorized Feishu chat after approval."
    source = "feishu"
    requires = ["feishu"]
    surfaces = (ToolSurface.ACTION,)
    side_effect_level = SideEffectLevel.EXTERNAL
    requires_approval = True
    approval_reason = "Replies to a Feishu message in the displayed exact chat."
    parallel_safe = False
    accepts_runtime_context = True
    log_omitted_input_fields = ("message", "target", "message_id")
    input_schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "minLength": 1,
                "description": "current, default, or an exact allowed chat_id:... target.",
            },
            "message_id": {
                "type": "string",
                "minLength": 1,
                "description": "Exact parent message ID in the approved chat.",
            },
            "message": {
                "type": "string",
                "minLength": 1,
                "description": "Complete Markdown reply body; no truncation.",
            },
            "reply_in_thread": {
                "type": "boolean",
                "description": "True for a topic/thread reply; false for a normal reply.",
            },
        },
        "required": ["target", "message_id", "message", "reply_in_thread"],
        "additionalProperties": False,
    }

    def is_available(self, sources: dict[str, dict]) -> bool:
        return has_outbound_target(sources, require_chat_id=True)

    def prepare_public_input(
        self, payload: dict[str, Any], resolved_integrations: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        return prepare_reply_input(payload, resolved_integrations)

    def run(
        self,
        target: str,
        message_id: str,
        message: str,
        reply_in_thread: bool,
        *,
        context: AgentToolContext,
    ) -> ToolExecutionResult:
        if not all((target.strip(), message_id.strip(), message.strip())):
            return failed_result(
                target=target,
                reply_to_message_id=message_id,
                error_type="validation",
                error="Feishu reply request is invalid",
            )
        if tool_resources_cancel_requested(context.resources):
            return failed_result(
                target=target,
                reply_to_message_id=message_id,
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
                require_chat_id=True,
            )
        except (ValueError, PermissionError):
            return failed_result(
                target=target,
                reply_to_message_id=message_id,
                error_type="authorization",
                error="Feishu reply target is no longer authorized",
            )

        if (
            lookup_reply_parent(
                credentials.app_id, credentials.app_secret, message_id, resolved.receive_id
            )
            is None
        ):
            return failed_result(
                target=resolved.canonical,
                reply_to_message_id=message_id,
                error_type="parent_unavailable",
                error="Feishu reply parent could not be verified in this chat",
            )
        if tool_resources_cancel_requested(context.resources):
            return failed_result(
                target=resolved.canonical,
                reply_to_message_id=message_id,
                error_type="cancelled",
                error="Feishu delivery was cancelled",
            )
        try:
            delivered = deliver_feishu_document(
                app_id=credentials.app_id,
                app_secret=credentials.app_secret,
                receive_id=resolved.receive_id,
                receive_id_type="chat_id",
                markdown=message,
                reply_to_message_id=message_id,
                reply_in_thread=reply_in_thread,
                cancel_requested=lambda: tool_resources_cancel_requested(context.resources),
                stop_on_ambiguous_write=True,
            )
        except Exception:
            return failed_result(
                target=resolved.canonical,
                reply_to_message_id=message_id,
                error_type="delivery_uncertain",
                error="Feishu reply could not be confirmed",
                attempted=True,
                certainty="maybe_sent",
            )
        return delivery_result(delivered, target=resolved.canonical, reply_to_message_id=message_id)


feishu_reply_message = tool(FeishuReplyMessageTool(), surfaces=(ToolSurface.ACTION,))

__all__ = ["FeishuReplyMessageTool", "feishu_reply_message"]
