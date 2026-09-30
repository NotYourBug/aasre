"""Gateway wording must agree with the exact tools offered this turn."""

from dataclasses import replace

from core.agent_harness.prompts import (
    build_action_system_prompt,
    build_action_system_prompt_envelope,
)
from core.agent_harness.turns.headless_adapters import InMemorySessionState
from infrastructure.harness_providers.prompt_context import ActionPromptContext
from integrations.harness_adapters import register_harness_adapters
from tests.core.agent_harness.test_gateway_channel_tools import snapshot


def prompt_for(platform: str | None, *tools: str) -> str:
    register_harness_adapters()
    turn = snapshot(InMemorySessionState(), surface="gateway", platform=platform)
    return build_action_system_prompt(
        turn, context=ActionPromptContext("gateway", platform, frozenset(tools))
    )


def test_send_only_feishu_prompt_has_only_offered_recipe() -> None:
    prompt = prompt_for("feishu", "feishu_send_message")
    assert "colleague in Feishu" in prompt
    assert "feishu_send_message" in prompt
    assert "feishu_reply_message" not in prompt
    assert "slack_send_message" not in prompt
    assert "interactive shell" not in prompt
    assert "skill_view" not in prompt


def test_reply_only_feishu_prompt_requires_known_parent() -> None:
    prompt = prompt_for("feishu", "feishu_reply_message")
    assert "feishu_reply_message" in prompt
    assert "known message_id" in prompt
    assert "feishu_send_message" not in prompt


def test_feishu_without_delivery_tools_makes_no_delivery_claim() -> None:
    prompt = prompt_for("feishu")
    assert "colleague in Feishu" in prompt
    assert "feishu_send_message" not in prompt
    assert "feishu_reply_message" not in prompt
    assert "extra approved" not in prompt


def test_other_gateways_never_inherit_slack_persona() -> None:
    for platform in (None, "discord", "telegram", "buzz", "unknown"):
        prompt = prompt_for(platform)
        assert "colleague in Slack" not in prompt
        assert "colleague in Feishu" not in prompt
        assert "interactive shell" not in prompt
        assert "slack_send_message" not in prompt


def test_gateway_base_preserves_shared_safety_and_completion_rules() -> None:
    prompt = prompt_for(None)
    for rule in (
        "tool results",
        "clarification",
        "authorization",
        "COMPOUND TURN",
        "investigation_start",
    ):
        assert rule in prompt


def test_connected_integrations_hide_inactive_delivery_sources() -> None:
    register_harness_adapters()
    session = InMemorySessionState(
        configured_integrations=["feishu", "slack", "rocketchat", "github"],
        configured_integrations_known=True,
    )
    turn = snapshot(session, surface="gateway", platform="feishu")
    context = ActionPromptContext("gateway", "feishu", frozenset())
    envelope = build_action_system_prompt_envelope(turn, context=context)
    connected = envelope.require_block("connected-integrations").content.splitlines()[0]
    assert connected.endswith("feishu, github")
    neutral = build_action_system_prompt_envelope(
        turn, context=replace(context, active_platform=None)
    )
    assert (
        neutral.require_block("connected-integrations").content.splitlines()[0].endswith("github")
    )
