"""Test: generate_report unmasks slack_message before delivery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _enable_masking(monkeypatch) -> None:
    monkeypatch.setenv("OPENSRE_MASK_ENABLED", "true")


def _state_with_masking() -> dict[str, object]:
    return {
        "alert_name": "pipeline failure",
        "severity": "warning",
        "problem_md": "# Incident in <NAMESPACE_0>",
        "slack_message": "",
        "report": "",
        "report_markdown": "",
        "masking_map": {
            "<POD_0>": "etl-worker-7d9f8b-xkp2q",
            "<NAMESPACE_0>": "tracer-test",
        },
        "root_cause": "etl-worker-7d9f8b-xkp2q OOMKilled in tracer-test",
        "evidence": {},
        "context": {},
        "resolved_integrations": {},
        "channel_contexts": {},
        "validated_claims": [],
        "non_validated_claims": [],
    }


def test_slack_message_is_unmasked_before_delivery() -> None:
    from tools.investigation.reporting import node as pub_node
    from tools.investigation.reporting.formatters.messages import ReportMessages

    masked_message = "Root cause: <POD_0> crashed in <NAMESPACE_0>. Impact: customer-facing."

    with (
        patch.object(pub_node, "build_report_context", return_value=MagicMock()),
        patch.object(
            pub_node,
            "build_report_messages",
            return_value=ReportMessages("Already rendered Markdown", masked_message, "tg", []),
        ),
        patch.object(pub_node, "render_report"),
        patch.object(pub_node, "open_in_editor"),
        patch.object(
            pub_node,
            "create_investigation_and_attach_url",
            return_value=("inv-123", "https://test/inv-123"),
        ),
        patch("integrations.slack.delivery.send_slack_report", return_value=(False, None)),
        patch("integrations.slack.delivery.build_action_blocks", return_value=[]),
    ):
        result = pub_node.generate_report(_state_with_masking())  # type: ignore[arg-type]

    assert "<POD_0>" not in result["slack_message"]
    assert "<NAMESPACE_0>" not in result["slack_message"]
    assert "etl-worker-7d9f8b-xkp2q" in result["slack_message"]
    assert "tracer-test" in result["slack_message"]

    assert result["report_markdown"] == "Already rendered Markdown"


def test_empty_masking_map_is_passthrough() -> None:
    from tools.investigation.reporting import node as pub_node
    from tools.investigation.reporting.formatters.messages import ReportMessages

    state = _state_with_masking()
    state["masking_map"] = {}
    message_without_placeholders = "Plain report with no placeholders."

    with (
        patch.object(pub_node, "build_report_context", return_value=MagicMock()),
        patch.object(
            pub_node,
            "build_report_messages",
            return_value=ReportMessages(
                message_without_placeholders,
                message_without_placeholders,
                "tg",
                [],
            ),
        ),
        patch.object(pub_node, "render_report"),
        patch.object(pub_node, "open_in_editor"),
        patch.object(
            pub_node,
            "create_investigation_and_attach_url",
            return_value=("inv-123", "https://test/inv-123"),
        ),
        patch("integrations.slack.delivery.send_slack_report", return_value=(False, None)),
        patch("integrations.slack.delivery.build_action_blocks", return_value=[]),
    ):
        result = pub_node.generate_report(state)  # type: ignore[arg-type]

    assert result["slack_message"] == message_without_placeholders
    assert result["report_markdown"] == message_without_placeholders


def test_real_markdown_renderer_restores_identifiers_before_escaping() -> None:
    from tools.investigation.reporting import node as pub_node

    state = _state_with_masking()
    state["root_cause"] = "<POD_0> failed in <NAMESPACE_0>"
    state["masking_map"] = {
        "<POD_0>": "worker_[link](https://example.test)",
        "<NAMESPACE_0>": "production",
    }
    with (
        patch.object(pub_node, "enrich_upstream_correlation", return_value={}),
        patch.object(pub_node, "create_investigation_and_attach_url", return_value=("", "")),
        patch.object(pub_node, "dispatch_report") as dispatch,
    ):
        result = pub_node.generate_report(state, render_terminal=False, open_editor=False)  # type: ignore[arg-type]

    markdown = result["report_markdown"]
    assert "production" in markdown
    assert r"worker\_\[link\]\(https://example.test\)" in markdown
    assert "POD" not in markdown
    assert "NAMESPACE" not in markdown
    assert dispatch.call_args.args[1].markdown_text == markdown
