"""Pure Card JSON 2.0 contracts for Feishu write-tool approvals."""

from __future__ import annotations

import json
from typing import cast

from integrations.feishu import render_approval_prompt_card, render_approval_result_card


def _elements(spec: dict[str, object]) -> list[dict[str, object]]:
    body = cast(dict[str, object], spec["body"])
    return cast(list[dict[str, object]], body["elements"])


def _buttons(spec: dict[str, object]) -> list[dict[str, object]]:
    column_set = next(element for element in _elements(spec) if element["tag"] == "column_set")
    columns = cast(list[dict[str, object]], column_set["columns"])
    return [cast(list[dict[str, object]], column["elements"])[0] for column in columns]


def test_prompt_has_distinct_callback_tokens_and_no_client_decision() -> None:
    spec = render_approval_prompt_card(
        approve_token="approve-token",
        deny_token="deny-token",
        tool_name="feishu_send_message",
        reason="Publish the incident summary",
        arguments_preview='{"channel": "ops"}',
    )
    buttons = _buttons(spec)

    assert spec["schema"] == "2.0"
    assert [
        cast(dict[str, str], button["text"])["content"] for button in buttons
    ] == ["Approve", "Deny"]
    assert [button["behaviors"] for button in buttons] == [
        [{"type": "callback", "value": {"approval_id": "approve-token"}}],
        [{"type": "callback", "value": {"approval_id": "deny-token"}}],
    ]
    assert all("name" not in button and "value" not in button for button in buttons)


def test_prompt_displays_tool_reason_and_only_the_redacted_preview() -> None:
    spec = render_approval_prompt_card(
        approve_token="approve-token",
        deny_token="deny-token",
        tool_name="feishu_send_message",
        reason="Publish the incident summary",
        arguments_preview='{"api_key": "[REDACTED]", "channel": "ops"}',
    )
    serialized = json.dumps(spec, ensure_ascii=False)

    assert "feishu_send_message" in serialized
    assert "Publish the incident summary" in serialized
    assert "[REDACTED]" in serialized
    assert "sk-raw-secret" not in serialized


def test_result_card_contains_only_safe_outcome_details() -> None:
    spec = render_approval_result_card(tool_name="dangerous_tool", approved=False)
    serialized = json.dumps(spec, ensure_ascii=False)

    assert spec["schema"] == "2.0"
    assert "Denied" in serialized
    assert "dangerous_tool" in serialized
    assert "button" not in serialized
    assert "approval_id" not in serialized
    assert "open_id" not in serialized
    assert "arguments" not in serialized.lower()


def test_approved_and_denied_results_use_distinct_headers() -> None:
    approved = render_approval_result_card(tool_name="write_tool", approved=True)
    denied = render_approval_result_card(tool_name="write_tool", approved=False)

    assert approved["header"] != denied["header"]
    assert "Approved" in json.dumps(approved)
    assert "Denied" in json.dumps(denied)
