"""Agent-facing Feishu writes retain scoped authority and approval."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.llm.types import ToolCall
from core.tool import RegisteredTool
from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    execute_tool_calls,
)
from gateway.core.middleware.approvals import arguments_preview
from integrations.feishu.tools.feishu_reply_message_tool import tool as reply_module
from integrations.feishu.tools.feishu_send_message_tool import tool as send_module
from integrations.feishu.tools.feishu_send_message_tool.tool import feishu_send_message


def test_canonical_target_stays_visible_in_approval_preview() -> None:
    previews: list[str] = []

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult:
        previews.append(arguments_preview(request.arguments))
        return BeforeToolCallResult(blocked=True, reason="do not dispatch")

    sources = {
        "feishu": {
            "app_id": "cli_test",
            "app_secret": "secret",
            "allowed_outbound_targets": "chat_id:oc_ops",
        }
    }
    calls = (
        (
            feishu_send_message,
            {"message": "x" * 1_000, "target": "chat_id:oc_ops"},
        ),
        (
            reply_module.feishu_reply_message,
            {
                "message": "x" * 1_000,
                "message_id": "om_parent",
                "target": "chat_id:oc_ops",
                "reply_in_thread": False,
            },
        ),
    )
    for candidate, arguments in calls:
        result = execute_tool_calls(
            [ToolCall(id="blocked-write", name=candidate.name, input=arguments)],
            [RegisteredTool.from_base_tool(candidate)],
            sources,
            hooks=ToolExecutionHooks(before_tool_call=before),
        )[0]
        assert result.is_error is True

    assert len(previews) == 2
    assert all('"target": "chat_id:oc_ops"' in preview for preview in previews)


def test_unauthorized_target_is_rejected_before_approval() -> None:
    approved: list[dict[str, Any]] = []

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult:
        approved.append(request.arguments)
        return BeforeToolCallResult(approved=True)

    result = execute_tool_calls(
        [
            ToolCall(
                id="send-1",
                name="feishu_send_message",
                input={"target": "chat_id:oc_other", "message": "private body"},
            )
        ],
        [RegisteredTool.from_base_tool(feishu_send_message)],
        {
            "feishu": {
                "app_id": "cli_test",
                "app_secret": "secret",
                "receive_id_type": "chat_id",
                "receive_id": "oc_default",
                "allowed_outbound_targets": "",
            }
        },
        hooks=ToolExecutionHooks(before_tool_call=before),
    )[0]

    assert result.is_error is True
    assert approved == []
    assert "unauthorized" in str(result.content).lower()


def test_missing_credentials_do_not_request_approval() -> None:
    approved: list[bool] = []

    def before(_request: ToolExecutionRequest) -> BeforeToolCallResult:
        approved.append(True)
        return BeforeToolCallResult(blocked=True, reason="test must not dispatch")

    result = execute_tool_calls(
        [
            ToolCall(
                id="send-unavailable",
                name="feishu_send_message",
                input={"target": "default", "message": "body"},
            )
        ],
        [RegisteredTool.from_base_tool(feishu_send_message)],
        {"feishu": {"receive_id_type": "chat_id", "receive_id": "oc_default"}},
        hooks=ToolExecutionHooks(before_tool_call=before),
    )[0]

    assert result.is_error is True
    assert approved == []


def test_reply_parent_in_another_chat_never_writes(monkeypatch) -> None:
    from integrations.feishu.credentials import FeishuChatCredentials

    def _credentials() -> FeishuChatCredentials:
        return FeishuChatCredentials(
            app_id="cli_test",
            app_secret="secret",
            receive_id="oc_approved",
            receive_id_type="chat_id",
        )

    def _no_parent(*_args: Any) -> None:
        return None

    def _never_deliver(**_kwargs: Any) -> None:
        raise AssertionError("mismatched parent must not be replied to")

    monkeypatch.setattr(reply_module, "load_chat_credentials_from_env", _credentials)
    monkeypatch.setattr(reply_module, "lookup_reply_parent", _no_parent)
    monkeypatch.setattr(reply_module, "deliver_feishu_document", _never_deliver)

    result = execute_tool_calls(
        [
            ToolCall(
                id="reply-1",
                name="feishu_reply_message",
                input={
                    "target": "default",
                    "message_id": "om_parent",
                    "message": "reply body",
                    "reply_in_thread": False,
                },
            )
        ],
        [RegisteredTool.from_base_tool(reply_module.feishu_reply_message)],
        {
            "feishu": {
                "app_id": "cli_test",
                "app_secret": "secret",
                "receive_id_type": "chat_id",
                "receive_id": "oc_approved",
                "allowed_outbound_targets": "",
            }
        },
        hooks=ToolExecutionHooks(
            before_tool_call=lambda _request: BeforeToolCallResult(approved=True)
        ),
    )[0]

    assert result.is_error is True
    assert result.details["status"] == "failed"
    assert result.details["attempted"] is False
    assert result.details["certainty"] == "definitely_not_sent"
    assert result.details["retry_safe"] is True


def test_changed_default_after_approval_fails_without_redirect(monkeypatch) -> None:
    from integrations.feishu.credentials import FeishuChatCredentials

    approved: list[str] = []

    def _credentials() -> FeishuChatCredentials:
        return FeishuChatCredentials(
            app_id="cli_test",
            app_secret="secret",
            receive_id="oc_new",
            receive_id_type="chat_id",
        )

    def _never_deliver(**_kwargs: Any) -> None:
        raise AssertionError("changed target must never be sent")

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult:
        approved.append(request.arguments["target"])
        return BeforeToolCallResult(approved=True)

    monkeypatch.setattr(send_module, "load_chat_credentials_from_env", _credentials)
    monkeypatch.setattr(send_module, "deliver_feishu_document", _never_deliver)
    result = execute_tool_calls(
        [
            ToolCall(
                id="send-default",
                name="feishu_send_message",
                input={"target": "default", "message": "body"},
            )
        ],
        [RegisteredTool.from_base_tool(feishu_send_message)],
        {
            "feishu": {
                "app_id": "cli_test",
                "app_secret": "secret",
                "receive_id_type": "chat_id",
                "receive_id": "oc_old",
                "allowed_outbound_targets": "",
            }
        },
        hooks=ToolExecutionHooks(before_tool_call=before),
    )[0]

    assert approved == ["chat_id:oc_old"]
    assert result.details["status"] == "failed"
    assert result.details["attempted"] is False


def test_tools_are_action_only_approved_and_hidden_in_other_gateway() -> None:
    send = RegisteredTool.from_base_tool(feishu_send_message)
    reply = RegisteredTool.from_base_tool(reply_module.feishu_reply_message)
    source = {
        "feishu": {"app_id": "cli_test", "app_secret": "secret"},
        "_gateway_platform": "slack",
        "_gateway_chat_id": "oc_untrusted",
    }

    for candidate in (send, reply):
        assert candidate.surfaces == (ToolSurface.ACTION,)
        assert candidate.requires_approval is True
        assert candidate.parallel_safe is False
        assert candidate.is_available(source) is False
        assert "app_secret" not in candidate.public_input_schema["properties"]
        assert "message" in candidate.log_omitted_input_fields
        assert "target" in candidate.log_omitted_input_fields

    source["_gateway_platform"] = "feishu"
    assert send.is_available(source) is True
    assert reply.is_available(source) is True


def test_approved_current_send_uses_exact_chat_and_complete_body(monkeypatch) -> None:
    from integrations.feishu.credentials import FeishuChatCredentials
    from integrations.feishu.delivery_types import FeishuDeliveryMode, FeishuDeliveryStatus
    from integrations.feishu.document_delivery import FeishuDocumentDeliveryResult

    approved: list[str] = []
    delivered: list[tuple[str, str, str]] = []

    def _credentials() -> FeishuChatCredentials:
        return FeishuChatCredentials(
            app_id="cli_test", app_secret="secret", receive_id="oc_default"
        )

    def _deliver(**kwargs: Any) -> FeishuDocumentDeliveryResult:
        assert kwargs["stop_on_ambiguous_write"] is True
        delivered.append((kwargs["receive_id"], kwargs["receive_id_type"], kwargs["markdown"]))
        return FeishuDocumentDeliveryResult(
            status=FeishuDeliveryStatus.SUCCESS,
            attempted=True,
            confirmed_message_ids=("om_sent",),
            delivery_mode=FeishuDeliveryMode.CARDS,
            error_category=None,
            error="",
        )

    def _approve(request: ToolExecutionRequest) -> BeforeToolCallResult:
        approved.append(request.arguments["target"])
        return BeforeToolCallResult(approved=True)

    monkeypatch.setattr(send_module, "load_chat_credentials_from_env", _credentials)
    monkeypatch.setattr(send_module, "deliver_feishu_document", _deliver)
    result = execute_tool_calls(
        [
            ToolCall(
                id="send-current",
                name="feishu_send_message",
                input={"target": "current", "message": "# whole body\n" + "x" * 5_000},
            )
        ],
        [RegisteredTool.from_base_tool(feishu_send_message)],
        {
            "feishu": {"app_id": "cli_test", "app_secret": "secret"},
            "_gateway_platform": "feishu",
            "_gateway_chat_id": "oc_current",
        },
        hooks=ToolExecutionHooks(before_tool_call=_approve),
    )[0]

    assert approved == ["chat_id:oc_current"]
    assert delivered == [("oc_current", "chat_id", "# whole body\n" + "x" * 5_000)]
    assert result.details["status"] == "sent"
    assert result.details["certainty"] == "confirmed_sent"
    assert result.details["confirmed_message_ids"] == ["om_sent"]


def test_partial_ambiguous_delivery_is_not_retry_safe() -> None:
    from dataclasses import replace

    from integrations.feishu.delivery_types import (
        FeishuDeliveryErrorCategory,
        FeishuDeliveryMode,
        FeishuDeliveryStatus,
    )
    from integrations.feishu.document_delivery import FeishuDocumentDeliveryResult
    from integrations.feishu.tools.results import delivery_result

    partial = FeishuDocumentDeliveryResult(
        status=FeishuDeliveryStatus.PARTIAL,
        attempted=True,
        confirmed_message_ids=("om_first",),
        delivery_mode=FeishuDeliveryMode.CARDS,
        error_category=FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN,
        error="Feishu delivery could not be confirmed",
    )

    result = delivery_result(partial, target="chat_id:oc_ops")
    definite = delivery_result(
        replace(partial, error_category=FeishuDeliveryErrorCategory.DEFINITE_REJECTION),
        target="chat_id:oc_ops",
    )

    assert result.is_error is True
    assert result.details["status"] == "partial"
    assert result.details["certainty"] == "maybe_sent"
    assert result.details["retry_safe"] is False
    assert result.details["confirmed_message_ids"] == ["om_first"]
    assert definite.details["status"] == "partial"
    assert definite.details["certainty"] == "partial"
    assert definite.details["retry_safe"] is False


def test_attempted_non_platform_failure_is_not_marked_retry_safe() -> None:
    from dataclasses import replace

    from integrations.feishu.delivery_types import (
        FeishuDeliveryErrorCategory,
        FeishuDeliveryMode,
        FeishuDeliveryStatus,
    )
    from integrations.feishu.document_delivery import FeishuDocumentDeliveryResult
    from integrations.feishu.tools.results import delivery_result

    failed = FeishuDocumentDeliveryResult(
        status=FeishuDeliveryStatus.FAILED,
        attempted=True,
        confirmed_message_ids=(),
        delivery_mode=FeishuDeliveryMode.TEXT_FALLBACK,
        error_category=FeishuDeliveryErrorCategory.TRANSPORT,
        error="Feishu delivery transport failed",
    )

    result = delivery_result(failed, target="chat_id:oc_ops")
    uncertain = delivery_result(
        replace(failed, error_category=FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN),
        target="chat_id:oc_ops",
    )
    rejected = delivery_result(
        replace(failed, error_category=FeishuDeliveryErrorCategory.DEFINITE_REJECTION),
        target="chat_id:oc_ops",
    )

    assert result.details["retry_safe"] is False
    assert uncertain.details["status"] == "failed"
    assert uncertain.details["certainty"] == "maybe_sent"
    assert uncertain.details["retry_safe"] is False
    assert rejected.details["retry_safe"] is True


def test_approved_reply_preserves_parent_and_thread_in_delivery(monkeypatch) -> None:
    from integrations.feishu.credentials import FeishuChatCredentials
    from integrations.feishu.delivery_types import FeishuDeliveryMode, FeishuDeliveryStatus
    from integrations.feishu.document_delivery import FeishuDocumentDeliveryResult
    from integrations.feishu.message_lookup import FeishuMessageMetadata

    observed: list[tuple[str, str, bool]] = []

    def _credentials() -> FeishuChatCredentials:
        return FeishuChatCredentials(
            app_id="cli_test", app_secret="secret", receive_id="oc_approved"
        )

    def _parent(_app_id: str, _secret: str, message_id: str, chat_id: str) -> FeishuMessageMetadata:
        observed.append((chat_id, message_id, False))
        return FeishuMessageMetadata(message_id, chat_id)

    def _deliver(**kwargs: Any) -> FeishuDocumentDeliveryResult:
        assert kwargs["stop_on_ambiguous_write"] is True
        observed.append(
            (
                kwargs["receive_id"],
                kwargs["reply_to_message_id"],
                kwargs["reply_in_thread"],
            )
        )
        return FeishuDocumentDeliveryResult(
            status=FeishuDeliveryStatus.SUCCESS,
            attempted=True,
            confirmed_message_ids=("om_reply",),
            delivery_mode=FeishuDeliveryMode.CARDS,
            error_category=None,
            error="",
        )

    monkeypatch.setattr(reply_module, "load_chat_credentials_from_env", _credentials)
    monkeypatch.setattr(reply_module, "lookup_reply_parent", _parent)
    monkeypatch.setattr(reply_module, "deliver_feishu_document", _deliver)

    result = execute_tool_calls(
        [
            ToolCall(
                id="reply-success",
                name="feishu_reply_message",
                input={
                    "target": "default",
                    "message_id": "om_parent",
                    "message": "reply body",
                    "reply_in_thread": True,
                },
            )
        ],
        [RegisteredTool.from_base_tool(reply_module.feishu_reply_message)],
        {
            "feishu": {
                "app_id": "cli_test",
                "app_secret": "secret",
                "receive_id_type": "chat_id",
                "receive_id": "oc_approved",
            }
        },
        hooks=ToolExecutionHooks(
            before_tool_call=lambda _request: BeforeToolCallResult(approved=True)
        ),
    )[0]

    assert observed == [("oc_approved", "om_parent", False), ("oc_approved", "om_parent", True)]
    assert result.details["status"] == "sent"
    assert result.details["reply_to_message_id"] == "om_parent"
