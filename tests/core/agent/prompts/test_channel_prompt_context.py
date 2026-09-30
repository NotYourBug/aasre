"""Only frozen host metadata can select the gateway's channel."""

import pytest

from core.agent_harness.prompts.kernel.channel_context import build_action_prompt_context


@pytest.mark.parametrize("platform", [None, [], {"platform": "feishu"}, "unknown"])
def test_missing_platform_stays_neutral_gateway(platform: object) -> None:
    context = build_action_prompt_context(
        surface="gateway",
        resolved_integrations={"_gateway_platform": platform, "feishu": {}, "slack": {}},
        offered_tool_names=frozenset(),
    )
    assert context.surface == "gateway"
    assert context.active_platform is None
