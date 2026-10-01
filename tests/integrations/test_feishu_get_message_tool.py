"""Read execution rejects injected authority and releases only sanitized results."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from lark_oapi.api.im.v1 import GetMessageRequest

from config.constants.feishu import (
    FEISHU_MESSAGE_READ_MAX_ID_CHARS,
    FEISHU_MESSAGE_READ_MAX_OUTPUT_CHARS,
    FEISHU_RATE_LIMIT_ERROR_CODES,
)
from config.constants.tool_policy import GATEWAY_ONLY_TOOL_TAG
from core.llm.types import ToolCall
from core.tool import AgentToolContext, RegisteredTool, SideEffectLevel
from core.tool.execution import execute_tool_calls
from infrastructure.harness_providers.prompt_context import (
    ACTION_PROMPT_CONTEXT_RESOURCE,
    ActionPromptContext,
)
from integrations.feishu.credentials import FeishuChatCredentials
from integrations.feishu.tools.feishu_get_message_tool import tool as read_module
from integrations.feishu.tools.feishu_get_message_tool.tool import FeishuGetMessageTool
from tests.integrations.feishu_read_support import install_read_transport, message_payload
from tests.integrations.test_feishu_read_scope import valid_view


def read_resources() -> dict[str, Any]:
    return {
        ACTION_PROMPT_CONTEXT_RESOURCE: ActionPromptContext(
            "gateway", "feishu", frozenset({"feishu_get_message"})
        )
    }


def _execute(
    message_id: str = "om_known",
    *,
    view: dict[str, Any] | None = None,
    resources: dict[str, Any] | None = None,
) -> Any:
    return execute_tool_calls(
        [ToolCall(id="read", name="feishu_get_message", input={"message_id": message_id})],
        [RegisteredTool.from_base_tool(FeishuGetMessageTool())],
        view if view is not None else valid_view(),
        tool_resources=resources if resources is not None else read_resources(),
    )[0]


def test_public_input_cannot_inject_scope_or_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FeishuGetMessageTool()

    def respond(_request: GetMessageRequest) -> dict[str, Any]:
        raise AssertionError("invalid input must not GET")

    def credentials() -> FeishuChatCredentials:
        raise AssertionError("invalid authority must not load credentials")

    probe = install_read_transport(monkeypatch, respond)
    monkeypatch.setattr(read_module, "load_chat_credentials_from_env", credentials)
    payloads: list[dict[str, Any]] = [
        {},
        {"message_id": 1},
        {"message_id": " "},
        {"message_id": "x" * (FEISHU_MESSAGE_READ_MAX_ID_CHARS + 1)},
    ]
    payloads.extend(
        {"message_id": "om_known", extra: "injected"}
        for extra in ("target", "chat_id", "token", "app_id", "app_secret")
    )
    for payload in payloads:
        _prepared, error = tool.prepare_public_input(payload, valid_view())
        assert error is not None and "injected" not in error
    prepared, error = tool.prepare_public_input({"message_id": " om_known "}, valid_view())
    assert prepared == {"message_id": "om_known"} and error is None
    for resources in (
        {},
        {
            ACTION_PROMPT_CONTEXT_RESOURCE: ActionPromptContext(
                "interactive_shell", None, frozenset()
            )
        },
    ):
        result = tool.run(message_id="om_known", context=AgentToolContext(valid_view(), resources))
        assert result.is_error and result.details["error_type"] == "authorization"
    write_only = {
        "feishu": {
            "app_id": "cli_test",
            "app_secret": "fake-secret",
            "receive_id": "oc_default",
            "allowed_outbound_targets": "chat_id:oc_other",
        }
    }
    assert tool.prepare_public_input({"message_id": "om_known"}, write_only)[1] is not None
    for invalid_id in (None, 42, "", " ", "x" * (FEISHU_MESSAGE_READ_MAX_ID_CHARS + 1)):
        result = tool.run(
            message_id=invalid_id, context=AgentToolContext(valid_view(), read_resources())
        )
        assert result.is_error and result.details["error_type"] == "validation"
    assert not probe.requests


def test_execution_matches_frozen_app_and_returns_only_safe_read_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = "cli_changed"

    def credentials() -> FeishuChatCredentials:
        return FeishuChatCredentials(
            app_id=app_id, app_secret="fake-secret", receive_id="oc_default"
        )

    def respond(request: GetMessageRequest) -> dict[str, Any]:
        assert request.message_id == "om_known"
        return message_payload(sender={"id": "ou_sender"}, tenant_key="tenant-private")

    monkeypatch.setattr(read_module, "load_chat_credentials_from_env", credentials)
    probe = install_read_transport(monkeypatch, respond)
    mismatch = _execute()
    assert mismatch.is_error and mismatch.details["error_type"] == "authorization"
    assert not probe.requests
    app_id = "cli_test"
    result = _execute(" om_known ")
    assert result.is_error is False and json.loads(result.content) == result.details
    assert result.details == {
        "source": "feishu",
        "status": "read",
        "message_id": "om_known",
        "chat_id": "oc_current",
        "msg_type": "interactive",
        "body_content": '{"schema":"2.0","body":{"elements":[]}}',
        "body_format": "json",
        "truncated": False,
        "redacted": False,
        "resource_content_included": False,
        "content_trust": "untrusted",
    }
    assert "fake-secret" not in repr(result) and "tenant-private" not in repr(result)
    registered = RegisteredTool.from_base_tool(FeishuGetMessageTool())
    assert registered.side_effect_level is SideEffectLevel.READ_ONLY
    assert not registered.requires_approval and registered.parallel_safe
    assert GATEWAY_ONLY_TOOL_TAG in registered.tags
    assert registered.log_omitted_input_fields == ("message_id",)
    assert registered.input_schema["properties"]["message_id"]["maxLength"] == 256
    direct = FeishuGetMessageTool().run(
        message_id=" " * 300 + "om_known" + " " * 300,
        context=AgentToolContext(valid_view(), read_resources()),
    )
    assert not direct.is_error and len(probe.requests) == 2


def test_error_and_truncated_details_cannot_retain_original_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign = False
    rate_limited = False

    def credentials() -> FeishuChatCredentials:
        return FeishuChatCredentials(app_id="cli_test", app_secret="fake-secret", receive_id="")

    def respond(_request: GetMessageRequest) -> dict[str, Any]:
        if rate_limited:
            return {
                "code": next(iter(FEISHU_RATE_LIMIT_ERROR_CODES)),
                "msg": "PROVIDER-CANARY oc_foreign",
            }
        return message_payload(
            chat_id="oc_foreign" if foreign else "oc_current",
            body={
                "content": json.dumps(
                    {"text": "Bearer SECRET-CANARY-123456 " + "x" * 13000 + "TAIL-CANARY"}
                )
            },
        )

    reports: list[Exception] = []

    def report(exc: Exception, **_kwargs: Any) -> None:
        reports.append(exc)

    monkeypatch.setattr(read_module, "load_chat_credentials_from_env", credentials)
    monkeypatch.setattr(read_module, "report_run_error", report, raising=False)
    probe = install_read_transport(monkeypatch, respond)
    result = _execute()
    assert not result.is_error and result.details["truncated"] and result.details["redacted"]
    assert result.details["body_format"] == "json_prefix"
    assert len(result.details["body_content"]) == FEISHU_MESSAGE_READ_MAX_OUTPUT_CHARS
    assert "SECRET-CANARY" not in repr(result) and "TAIL-CANARY" not in repr(result)
    foreign = True
    rejected = _execute()
    assert rejected.is_error and set(rejected.details) == {
        "source",
        "status",
        "error_type",
        "error",
    }
    assert "oc_foreign" not in repr(rejected) and "CANARY" not in repr(rejected)
    assert not reports and len(probe.requests) == 2

    rate_limited = True
    rejected = _execute()
    assert rejected.is_error and json.loads(rejected.content) == rejected.details
    assert rejected.details == {
        "source": "feishu",
        "status": "failed",
        "error_type": "rate_limited",
        "error": "Feishu read was rate limited; try again later",
    }
    assert not reports and len(probe.requests) == 3

    def broken_credentials() -> FeishuChatCredentials:
        raise RuntimeError("CREDENTIAL-CANARY")

    monkeypatch.setattr(read_module, "load_chat_credentials_from_env", broken_credentials)
    rejected = _execute()
    assert rejected.is_error and "CANARY" not in repr(rejected)
    assert len(reports) == 1 and reports[0].args == ("RuntimeError",)
    assert reports[0].__context__ is None
    cancelled = read_resources()
    cancelled["cancel"] = SimpleNamespace(console=SimpleNamespace(cancel_requested=True))
    result = _execute(resources=cancelled)
    assert result.details["error_type"] == "cancelled" and len(probe.requests) == 3
