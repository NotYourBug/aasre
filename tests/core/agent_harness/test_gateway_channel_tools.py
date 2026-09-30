"""The final action-driver gate applies even to precomputed tool providers."""

from dataclasses import replace
from typing import Any

import pytest

from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.action_driver import ActionTurnRunner, _StaticToolCallLLM
from core.agent_harness.turns.headless_adapters import BufferOutputSink, InMemorySessionState
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.domain.types.tools import ToolSurface
from core.llm.types import AgentLLMResponse
from core.tool import RegisteredTool
from infrastructure.harness_providers import ToolSources
from integrations.harness_adapters import register_harness_adapters


class RecordingLLM(_StaticToolCallLLM):
    def __init__(self) -> None:
        super().__init__([])
        self.offered: list[str] = []
        self.systems: list[str] = []

    def tool_schemas(self, tools: Any) -> list[dict[str, Any]]:
        self.offered = [tool.name for tool in tools]
        return []

    def invoke(
        self, _messages: Any, *, system: str | None = None, tools: Any = None
    ) -> AgentLLMResponse:
        _ = tools
        self.systems.append(system or "")
        return AgentLLMResponse(content="Recorded answer.", tool_calls=[], raw_content=None)


def candidate(name: str, source: str) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        source=source,
        description="Offline recording tool",
        input_schema={"type": "object", "properties": {}},
        run=lambda: {"ok": True},
    )


def snapshot(session: InMemorySessionState, *, surface: str, platform: object) -> TurnSnapshot:
    return replace(
        TurnSnapshot.from_session("record", session, surface=surface),
        resolved_integrations={"_gateway_platform": platform, "_gateway_chat_id": "private-chat"},
    )


@pytest.mark.parametrize(
    "surface,platform,expected",
    [
        ("gateway", "feishu", ["opaque-a", "ordinary"]),
        ("gateway", "unknown", ["ordinary"]),
        ("interactive_shell", "feishu", ["opaque-a", "opaque-b", "opaque-c", "ordinary"]),
    ],
)
def test_precomputed_provider_cannot_bypass_final_gate(
    surface: str, platform: str, expected: list[str]
) -> None:
    register_harness_adapters()
    session = InMemorySessionState()
    candidates = [
        candidate("opaque-a", "feishu"),
        candidate("opaque-b", "slack"),
        candidate("opaque-c", "rocketchat"),
        candidate("ordinary", "github"),
    ]
    provider = DefaultToolProvider(session, None, precomputed_action_tools=candidates)
    llm = RecordingLLM()
    result = ActionTurnRunner(BufferOutputSink(), provider, lambda: llm).run(
        "record", session, turn_plan=TurnPlan(snapshot(session, surface=surface, platform=platform))
    )
    assert result.accounting_status == "completed", result.response_text
    assert llm.offered == expected
    assert [tool.name for tool in candidates] == ["opaque-a", "opaque-b", "opaque-c", "ordinary"]


def test_normal_provider_keeps_availability_checks_before_channel_gate() -> None:
    register_harness_adapters()
    seen: list[dict[str, Any]] = []

    def unavailable(sources: dict[str, Any]) -> bool:
        seen.append(sources)
        return False

    candidates = [
        candidate("active", "feishu"),
        candidate("inactive", "slack"),
        replace(candidate("withheld", "feishu"), is_available=unavailable),
        candidate("ordinary", "github"),
    ]

    class OfflineRegistry:
        def tools_for_surface(self, _surface: ToolSurface) -> list[RegisteredTool]:
            return candidates

        def tool_map_for_surface(self, _surface: ToolSurface) -> dict[str, RegisteredTool]:
            return {tool.name: tool for tool in candidates}

    ToolSources(registry=OfflineRegistry()).install()
    session = InMemorySessionState()
    turn = replace(
        snapshot(session, surface="gateway", platform="feishu"),
        resolved_integrations={
            "_gateway_platform": "feishu",
            "_gateway_chat_id": "private-chat",
            "feishu": {"connection_verified": True},
            "slack": {"connection_verified": True},
        },
    )
    llm = RecordingLLM()
    provider = DefaultToolProvider(session, None)
    result = ActionTurnRunner(BufferOutputSink(), provider, lambda: llm).run(
        "record", session, turn_plan=TurnPlan(turn)
    )
    assert result.accounting_status == "completed", result.response_text
    assert len(seen) == 1
    assert "feishu" in seen[0] and "slack" in seen[0]
    assert llm.offered == ["active", "ordinary"]
