"""Pin real execution authority, bounded JSON, coverage and safe telemetry."""

import json
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from lark_oapi.api.im.v1 import ListMessageRequest

from config.constants.tool_policy import GATEWAY_ONLY_TOOL_TAG
from core.llm.types import ToolCall
from core.tool import AgentToolContext, RegisteredTool, SideEffectLevel
from core.tool.execution import execute_tool_calls
from infrastructure.harness_providers.prompt_context import (
    ACTION_PROMPT_CONTEXT_RESOURCE,
    ActionPromptContext,
)
from integrations.feishu.credentials import FeishuChatCredentials
from integrations.feishu.search_types import (
    FeishuSearchItem,
    FeishuSearchResult,
    FeishuSearchStopReason,
)
from integrations.feishu.tools.feishu_search_messages_tool import tool as search_module
from integrations.feishu.tools.feishu_search_messages_tool.results import search_success
from integrations.feishu.tools.feishu_search_messages_tool.tool import FeishuSearchMessagesTool
from tests.integrations.feishu_search_support import (
    install_search_transport,
    search_item,
    search_page,
)
from tests.integrations.test_feishu_read_scope import valid_view


def search_resources() -> dict[str, Any]:
    return {
        ACTION_PROMPT_CONTEXT_RESOURCE: ActionPromptContext(
            "gateway", "feishu", frozenset({"feishu_search_messages"})
        )
    }


def _execute(
    *,
    payload: dict[str, Any] | None = None,
    view: dict[str, Any] | None = None,
    resources: dict[str, Any] | None = None,
) -> Any:
    return execute_tool_calls(
        [
            ToolCall(
                id="search",
                name="feishu_search_messages",
                input=payload if payload is not None else {"start_time": 1000, "end_time": 2000},
            )
        ],
        [RegisteredTool.from_base_tool(FeishuSearchMessagesTool())],
        view if view is not None else valid_view(),
        tool_resources=resources if resources is not None else search_resources(),
    )[0]


def test_real_executor_rejects_injected_authority_before_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_reads: list[str] = []

    def credentials() -> FeishuChatCredentials:
        credential_reads.append("load")
        raise AssertionError("credentials must not load")

    def respond(_request: ListMessageRequest) -> dict[str, Any]:
        raise AssertionError("history must not GET")

    monkeypatch.setattr(search_module, "load_chat_credentials_from_env", credentials, raising=False)
    probe = install_search_transport(monkeypatch, respond)
    payloads = [
        {"start_time": True, "end_time": 2000},
        {"start_time": 1000, "end_time": int(time.time()) + 60},
    ]
    payloads.extend(
        {"start_time": 1000, "end_time": 2000, field: "INJECTED"}
        for field in ("chat_id", "target", "token", "page_token", "page_size", "app_id", "context")
    )
    for payload in payloads:
        result = _execute(payload=payload)
        assert result.is_error and "INJECTED" not in result.content
    for resources in (
        {},
        {
            ACTION_PROMPT_CONTEXT_RESOURCE: ActionPromptContext(
                "interactive_shell", None, frozenset()
            )
        },
        {ACTION_PROMPT_CONTEXT_RESOURCE: ActionPromptContext("gateway", "telegram", frozenset())},
    ):
        result = _execute(resources=resources)
        assert result.is_error and result.details["error_type"] == "authorization"
        direct = FeishuSearchMessagesTool().run(
            start_time=1000, end_time=2000, context=AgentToolContext(valid_view(), resources)
        )
        assert direct.is_error and direct.details["error_type"] == "authorization"
    view = valid_view()
    view["feishu"] = {**view["feishu"].model_dump(), "connection_verified": False}
    assert _execute(view=view).is_error
    assert not probe.requests and not credential_reads


def _result(items: tuple[FeishuSearchItem, ...] = ()) -> FeishuSearchResult:
    return FeishuSearchResult(
        "oc_current",
        1000,
        2000,
        items,
        1,
        len(items),
        len(items),
        0,
        True,
        FeishuSearchStopReason.SOURCE_EXHAUSTED,
    )


def test_search_result_bounds_escaped_json_and_keeps_details_identical() -> None:
    items = tuple(
        FeishuSearchItem(f"om_{n}", "text", 1500000, "\x01" * 600, True, True, False)
        for n in range(20)
    )
    encoded = search_success(_result(items))
    assert not encoded.is_error and len(encoded.content) <= 20000
    assert json.loads(encoded.content) == encoded.details
    payload = encoded.details
    assert 0 < payload["returned_count"] == len(payload["items"]) < 20
    assert (
        payload["results_truncated"] and not payload["complete"] and payload["matched_count"] == 20
    )
    assert all(item["preview"] == "\x01" * 600 for item in payload["items"])
    limited = search_success(replace(_result(items[:1]), matched_count=3, scanned_count=3))
    assert limited.details["matched_count"] == 3 and limited.details["results_truncated"]
    assert not {"query", "page_token", "next_page_token"} & set(payload)


@pytest.mark.parametrize(
    "scan_complete,unsearchable,want_complete",
    [(True, 0, True), (False, 0, False), (True, 1, False)],
)
def test_complete_flags_do_not_turn_an_empty_partial_scan_into_no_matches(
    scan_complete: bool, unsearchable: int, want_complete: bool
) -> None:
    result = replace(
        _result(),
        scan_complete=scan_complete,
        unsearchable_count=unsearchable,
        stop_reason=FeishuSearchStopReason.SOURCE_EXHAUSTED
        if scan_complete
        else FeishuSearchStopReason.PAGE_LIMIT,
    )
    payload = search_success(result).details
    assert payload["complete"] is want_complete and payload["content_complete"] is (
        unsearchable == 0
    )
    assert payload["coverage"] == "current_chat_container"
    assert (
        payload["thread_replies_included"] is False
        and payload["resource_content_included"] is False
    )
    assert payload["content_trust"] == "untrusted"


def test_unexpected_tool_failure_reports_only_an_unchained_exception_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def credentials() -> FeishuChatCredentials:
        raise RuntimeError("CREDENTIAL-CANARY")

    reports: list[Exception] = []

    def report(exc: Exception, **_kwargs: Any) -> None:
        reports.append(exc)

    monkeypatch.setattr(search_module, "load_chat_credentials_from_env", credentials, raising=False)
    monkeypatch.setattr(search_module, "report_run_error", report, raising=False)
    result = _execute()
    assert result.is_error and result.details["error_type"] == "upstream_error"
    assert len(reports) == 1 and reports[0].args == ("RuntimeError",)
    assert reports[0].__context__ is None and reports[0].__cause__ is None
    assert "CREDENTIAL-CANARY" not in repr(result)


def test_executor_releases_only_current_chat_safe_metadata_and_checks_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = "cli_wrong"

    def credentials() -> FeishuChatCredentials:
        return FeishuChatCredentials(app_id=app_id, app_secret="fake-secret", receive_id="")

    def respond(_request: ListMessageRequest) -> dict[str, Any]:
        return search_page(
            [
                search_item(
                    sender={"id": "PRIVATE-SENDER"},
                    tenant_key="PRIVATE-TENANT",
                    body={"content": '{"text":"故障","token":"PRIVATE-TOKEN"}'},
                )
            ]
        )

    monkeypatch.setattr(search_module, "load_chat_credentials_from_env", credentials, raising=False)
    probe = install_search_transport(monkeypatch, respond)
    assert _execute().details["error_type"] == "authorization" and not probe.requests
    app_id = "cli_test"
    result = _execute(payload={"start_time": 1000, "end_time": 2000, "query": "故障"})
    assert not result.is_error and json.loads(result.content) == result.details
    assert result.details["complete"] and result.details["items"][0]["redacted"]
    assert result.details["items"][0]["message_id"] == "om_known"
    for secret in ("PRIVATE-SENDER", "PRIVATE-TENANT", "PRIVATE-TOKEN", "fake-secret"):
        assert secret not in repr(result)
    registered = RegisteredTool.from_base_tool(FeishuSearchMessagesTool())
    assert registered.side_effect_level is SideEffectLevel.READ_ONLY and registered.parallel_safe
    assert not registered.requires_approval and GATEWAY_ONLY_TOOL_TAG in registered.tags
    assert registered.log_omitted_input_fields == ("query", "start_time", "end_time")
    assert registered.input_schema["required"] == ["start_time", "end_time"]
    cancelled = {
        **search_resources(),
        "cancel": SimpleNamespace(console=SimpleNamespace(cancel_requested=True)),
    }
    assert _execute(resources=cancelled).details["error_type"] == "cancelled"
    assert len(probe.requests) == 1


def test_cancellation_during_encoding_discards_the_ready_search_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = SimpleNamespace(cancel_requested=False)
    resources = {**search_resources(), "cancel": SimpleNamespace(console=console)}
    encode = search_module.search_success

    def cancelled_encoding(result: FeishuSearchResult) -> Any:
        encoded = encode(result)
        console.cancel_requested = True
        return encoded

    def credentials() -> FeishuChatCredentials:
        return FeishuChatCredentials(app_id="cli_test", app_secret="fake-secret", receive_id="")

    def respond(_request: ListMessageRequest) -> dict[str, Any]:
        return search_page([search_item()])

    monkeypatch.setattr(search_module, "load_chat_credentials_from_env", credentials)
    monkeypatch.setattr(search_module, "search_success", cancelled_encoding)
    probe = install_search_transport(monkeypatch, respond)
    result = _execute(resources=resources)
    assert result.is_error and result.details["error_type"] == "cancelled"
    assert "items" not in result.details and len(probe.requests) == 1
