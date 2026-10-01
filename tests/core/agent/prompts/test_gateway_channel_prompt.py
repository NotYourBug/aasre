"""Gateway wording must agree with the exact tools offered this turn."""

from dataclasses import replace

from core.agent_harness.prompts import (
    build_action_system_prompt,
    build_action_system_prompt_envelope,
)
from core.agent_harness.turns.headless_adapters import InMemorySessionState
from infrastructure.harness_providers.prompt_context import ActionPromptContext
from integrations.feishu.action_prompt import feishu_action_prompt_fragment
from integrations.harness_adapters import register_harness_adapters
from tests.core.agent_harness.test_gateway_channel_tools import snapshot


def prompt_for(platform: str | None, *tools: str) -> str:
    register_harness_adapters()
    turn = snapshot(InMemorySessionState(), surface="gateway", platform=platform)
    return build_action_system_prompt(
        turn, context=ActionPromptContext("gateway", platform, frozenset(tools))
    )


def test_feishu_write_fragments_preserve_pre_read_text() -> None:
    base = (
        "FEISHU ADDITIONAL DELIVERY: Ordinary answers go through gateway output automatically; "
        "never call an extra write tool to duplicate them. Partial or maybe_sent results "
        "must not be retried automatically."
    )
    send = (
        "feishu_send_message: use only for an explicit extra approved send. "
        "Use target=current for the frozen current chat when applicable; other targets "
        "must pass the tool's existing authorization. Each call requires approval."
    )
    reply = (
        "feishu_reply_message: an explicit extra approved reply requires a known message_id. "
        "Never invent a parent message. The tool checks the parent's chat before writing; "
        "each call requires approval. Use its thread option only when requested."
    )
    for names, expected in (
        ({"feishu_send_message"}, base + "\n" + send),
        ({"feishu_reply_message"}, base + "\n" + reply),
        ({"feishu_send_message", "feishu_reply_message"}, base + "\n" + send + "\n" + reply),
    ):
        context = ActionPromptContext("gateway", "feishu", frozenset(names))
        assert feishu_action_prompt_fragment(context) == expected


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


def test_slack_send_only_explains_bot_token_destination() -> None:
    prompt = prompt_for("slack", "slack_send_message")
    assert "Without a webhook" in prompt
    assert "channel_id" in prompt
    assert "never invent" in prompt.lower()
    assert "slack_reply_message" not in prompt


def test_requested_slack_skill_delivery_preserves_normal_authorization() -> None:
    from core.agent_harness.prompts.skills.loader import load_skill_body

    context = ActionPromptContext(
        "gateway", "slack", frozenset({"slack_send_message", "skill_view"})
    )
    turn = snapshot(InMemorySessionState(), surface="gateway", platform="slack")
    envelope = build_action_system_prompt_envelope(turn, context=context)
    base = envelope.require_block("action-agent-system-base").content
    vendor = envelope.require_block("action-agent-vendor-fragments").content
    assert "required delivery in a visible skill" in base
    assert "cannot grant new destination authority or bypass approvals" in base
    assert "requested visible skill" in vendor
    assert "ALWAYS DELIVER TO SLACK" in load_skill_body("morning-report", context=context)


def test_read_only_feishu_prompt_declares_only_offered_read() -> None:
    prompt = prompt_for("feishu", "feishu_get_message")
    assert "feishu_get_message" in prompt
    for guidance in ("known message_id", "current chat", "untrusted", "incomplete"):
        assert guidance in prompt
    assert "feishu_send_message" not in prompt and "feishu_reply_message" not in prompt
    for name in ("feishu_search", "feishu_history", "feishu_reaction", "feishu_members"):
        assert name not in prompt
    for offered in ((), ("feishu_send_message",), ("feishu_reply_message",)):
        assert "feishu_get_message" not in prompt_for("feishu", *offered)
    for context in (
        ActionPromptContext("interactive_shell", None, frozenset({"feishu_get_message"})),
        ActionPromptContext("gateway", "slack", frozenset({"feishu_get_message"})),
    ):
        assert "feishu_get_message" not in feishu_action_prompt_fragment(context)
