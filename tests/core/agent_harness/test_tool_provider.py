"""Ordinary action logs must not copy sensitive declared tool arguments."""

from __future__ import annotations

import logging

import pytest

from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.tool import RegisteredTool
from integrations.feishu.tools.feishu_send_message_tool.tool import feishu_send_message


def test_omitted_message_and_target_do_not_reach_action_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.feishu.action_log")
    provider = DefaultToolProvider(
        session=object(),
        console=object(),
        precomputed_action_tools=[RegisteredTool.from_base_tool(feishu_send_message)],
        tool_action_logger=logger,
    )
    provider.action_tools(confirm_fn=None, is_tty=False)
    caplog.set_level(logging.INFO, logger=logger.name)

    provider.observer(message="")(
        "tool_start",
        {
            "name": "feishu_send_message",
            "input": {"target": "chat_id:oc_private", "message": "private outbound body"},
        },
    )

    assert "private outbound body" not in caplog.text
    assert "oc_private" not in caplog.text
    assert "feishu_send_message" in caplog.text
